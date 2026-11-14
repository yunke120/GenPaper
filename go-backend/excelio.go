package main

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"log"
	"math"
	"regexp"
	"strconv"
	"strings"

	"github.com/xuri/excelize/v2"
)

// ======================================================================
// Excel 数据解析（对应 Python 版 parse_excel_data）
// ======================================================================

// findSheet 精确匹配优先，其次包含匹配
func findSheet(names []string, keyword string) string {
	for _, n := range names {
		if strings.TrimSpace(n) == keyword {
			return n
		}
	}
	for _, n := range names {
		if strings.Contains(n, keyword) {
			return n
		}
	}
	return ""
}

// parseExcelData 解析数据包，提取批次/参数/文档数据
func parseExcelData(path string) (*ParsedData, error) {
	f, err := excelize.OpenFile(path)
	if err != nil {
		return nil, fmt.Errorf("解析Excel文件失败: %v", err)
	}
	defer f.Close()

	sheets := f.GetSheetList()
	dataSheet := findSheet(sheets, "数据")
	if dataSheet == "" {
		return nil, fmt.Errorf("解析Excel文件失败: 在Excel文件中未找到'数据'sheet")
	}
	docSheet := findSheet(sheets, "文档")
	log.Printf("找到数据sheet: %s", dataSheet)
	if docSheet != "" {
		log.Printf("找到文档sheet: %s", docSheet)
	}

	out := &ParsedData{Documents: &DocData{
		Generic:       map[string]DocItem{},
		BatchDocs:     map[string]map[string]DocItem{},
		BatchPrefixes: map[string]bool{},
	}}

	// ---------- 数据 sheet ----------
	rows, err := f.GetRows(dataSheet)
	if err != nil {
		return nil, fmt.Errorf("解析Excel文件失败: %v", err)
	}

	// 批次号在第 1 行（0 基第 0 行）从第 4 列（0 基第 3 列）起，同时记录列号
	type batchCol struct{ name string; col int }
	var batchCols []batchCol
	if len(rows) > 0 {
		for c := 3; c < len(rows[0]); c++ {
			name := strings.TrimSpace(rows[0][c])
			if name != "" {
				batchCols = append(batchCols, batchCol{name, c})
			}
		}
	}
	for i, bc := range batchCols {
		if bc.col != 3+i {
			log.Printf("警告: 数据sheet 批次列不连续（'%s' 位于第 %d 列，预期第 %d 列），参数取值已按实际列号对应",
				bc.name, bc.col+1, 3+i+1)
		}
	}
	for _, bc := range batchCols {
		out.BatchNumbers = append(out.BatchNumbers, bc.name)
	}
	log.Printf("发现批次号: %v", out.BatchNumbers)

	// 参数从第 2 行（0 基第 1 行）起：B 列 ID、C 列描述
	identifierRe := regexp.MustCompile(`^[^\W\d][\w]*$`)
	for r := 1; r < len(rows); r++ {
		row := rows[r]
		if len(row) <= 1 {
			continue
		}
		id := strings.TrimSpace(row[1])
		if id == "" || id == "ID" {
			continue
		}
		desc := ""
		if len(row) > 2 {
			desc = strings.TrimSpace(row[2])
		}
		p := &Param{ID: id, Description: desc, Data: map[string]string{}}
		for _, bc := range batchCols {
			v := ""
			if bc.col < len(row) {
				v = strings.TrimSpace(row[bc.col])
			}
			p.Data[bc.name] = v
		}
		out.Parameters = append(out.Parameters, p)

		// Word 模板路径以参数 ID 作为变量名，提前给出约束警告
		if !identifierRe.MatchString(id) {
			log.Printf("警告: 参数 ID '%s' 不是合法标识符，Word 模板中将无法通过 {{%s}} 引用（Excel 模板不受影响）", id, id)
		}
	}
	log.Printf("提取到 %d 个参数", len(out.Parameters))

	// ---------- 文档 sheet ----------
	if docSheet == "" {
		return out, nil
	}
	drows, err := f.GetRows(docSheet)
	if err != nil {
		return out, nil
	}

	// 组识别：第 2 行（0 基第 1 行）出现 "ID" 处为一个组，组名在第 1 行
	type groupInfo struct {
		cols    []int
		isBatch bool
	}
	var colCount int
	for _, r := range drows {
		if len(r) > colCount {
			colCount = len(r)
		}
	}
	cell := func(r, c int) string {
		if r < len(drows) && c < len(drows[r]) {
			return strings.TrimSpace(drows[r][c])
		}
		return ""
	}
	var batchGroup = "单机文档"
	groups := map[string]*groupInfo{}
	var groupOrder []string
	if colCount > 0 {
		for c := 0; c+2 < colCount; {
			if cell(1, c) == "ID" {
				name := cell(0, c)
				if name != "" {
					if batchRe.MatchString(name) {
						if groups[batchGroup] == nil {
							groups[batchGroup] = &groupInfo{isBatch: true}
							groupOrder = append(groupOrder, batchGroup)
						}
						groups[batchGroup].cols = append(groups[batchGroup].cols, c)
					} else {
						if groups[name] == nil {
							groups[name] = &groupInfo{}
							groupOrder = append(groupOrder, name)
						}
						groups[name].cols = append(groups[name].cols, c)
					}
				}
				c += 3
			} else {
				c++
			}
		}
	}
	log.Printf("自动识别到文档分组: %v", groupOrder)
	// 通用文档分组
	for _, name := range groupOrder {
		g := groups[name]
		if name == batchGroup || g.isBatch || len(g.cols) == 0 {
			continue
		}
		start := g.cols[0]
		log.Printf("%s数据位于列 %d-%d", name, start+1, start+3)
		for r := 2; r < len(drows); r++ {
			id := cell(r, start)
			if id == "" {
				continue
			}
			number, name2 := cell(r, start+1), cell(r, start+2)
			if number != "" && name2 != "" {
				out.Documents.Generic[id] = DocItem{Number: number, Name: name2}
			}
		}
	}

	// 单机文档批次
	if g := groups[batchGroup]; g != nil && len(g.cols) > 0 {
		var batchNames []string
		for _, c := range g.cols {
			if n := cell(0, c); n != "" {
				batchNames = append(batchNames, n)
			} else {
				batchNames = append(batchNames, fmt.Sprintf("批次%d", c))
			}
		}
		log.Printf("识别到 %d 个单机文档批次: %v", len(g.cols), batchNames)
		for i, c := range g.cols {
			batch := batchNames[i]
			docs := map[string]DocItem{}
			for r := 2; r < len(drows); r++ {
				id := cell(r, c)
				if id == "" {
					continue
				}
				number, name2 := cell(r, c+1), cell(r, c+2)
				if number != "" && name2 != "" {
					docs[id] = DocItem{Number: number, Name: name2}
				}
			}
			out.Documents.BatchDocs[batch] = docs
		}
		// 收集批次组下的文档 ID 前缀（如 Doc、Fxt），替代旧的硬编码 'Doc' 判断
		for _, docs := range out.Documents.BatchDocs {
			for key := range docs {
				if i := strings.LastIndex(key, "_"); i > 0 {
					out.Documents.BatchPrefixes[key[:i]] = true
				}
			}
		}
	}
	gDocs := len(out.Documents.Generic)
	bDocs := 0
	for _, docs := range out.Documents.BatchDocs {
		bDocs += len(docs)
	}
	log.Printf("文档数据: %d个通用文档, %d个单机文档", gDocs, bDocs)
	return out, nil
}

var batchRe = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9]*-[0-9]+$`)

// ======================================================================
// 单元格占位符替换（Excel 模板路径）
// ======================================================================

var placeholderRe = regexp.MustCompile(`\{\{([^{}]*)\}\}`)
var docCallRe = regexp.MustCompile(`^doc\(\s*['"]([^'"]+)['"]\s*\)$`)

// replaceCellPlaceholders 对应 _replace_excel_cell；返回 (新文本, 参数替换数, 文档替换数)
func replaceCellPlaceholders(value string, context map[string]string, d *DocData, batch string) (string, int, int) {
	pcount, dcount := 0, 0
	newValue := placeholderRe.ReplaceAllStringFunc(value, func(m string) string {
		name := strings.TrimSpace(placeholderRe.FindStringSubmatch(m)[1])
		if v, ok := context[name]; ok {
			pcount++
			return v
		}
		if v := getDocumentValue(d, name, batch); v != "" {
			dcount++
			return v
		}
		log.Printf("未找到数据或文档 {{{{%s}}}}", name)
		return m
	})
	return newValue, pcount, dcount
}

// warnExcelTemplateObjects 对应 _warn_excel_template_objects：图表/图片会丢失告警
func warnExcelTemplateObjects(path string) {
	r, err := zip.OpenReader(path)
	if err != nil {
		return
	}
	defer r.Close()
	count := 0
	for _, f := range r.File {
		if strings.HasPrefix(f.Name, "xl/charts/") || strings.HasPrefix(f.Name, "xl/media/") || strings.HasPrefix(f.Name, "xl/drawings/") {
			count++
		}
	}
	if count > 0 {
		log.Printf("警告: Excel 模板包含 %d 个图表/图片/绘图对象，openpyxl/excelize 保存后这些对象可能丢失", count)
	}
}

// generateExcelReport 对应 generate_excel_report：保留原结构样式，仅替换单元格占位符
func generateExcelReport(templatePath, batch string, params []*Param, d *DocData, out io.Writer, templateName string) (int, int, error) {
	ext := strings.ToLower(filepath2Ext(templateName))
	if ext == ".xls" {
		return 0, 0, fmt.Errorf("为保留原结构样式，Excel 模板请使用 .xlsx 格式，不支持直接编辑 .xls")
	}
	warnExcelTemplateObjects(templatePath)

	context := map[string]string{}
	for _, p := range params {
		v, ok := p.Data[batch]
		if !ok {
			v = fmt.Sprintf("[数据缺失: %s]", batch)
		}
		context[p.ID] = v
	}

	f, err := excelize.OpenFile(templatePath)
	if err != nil {
		return 0, 0, err
	}
	defer f.Close()

	pcount, dcount := 0, 0
	for _, sheet := range f.GetSheetList() {
		rows, err := f.GetRows(sheet)
		if err != nil {
			continue
		}
		for r := 0; r < len(rows); r++ {
			for c := 0; c < len(rows[r]); c++ {
				v := rows[r][c]
				if !strings.Contains(v, "{{") {
					continue
				}
				cell, _ := excelize.CoordinatesToCellName(c+1, r+1)
				if formula, _ := f.GetCellFormula(sheet, cell); formula != "" {
					// 公式单元格仅保留原样，避免替换内容破坏公式结构
					continue
				}
				nv, pc, dc := replaceCellPlaceholders(v, context, d, batch)
				pcount += pc
				dcount += dc
				if nv != v {
					_ = f.SetCellValue(sheet, cell, nv)
				}
			}
		}
	}
	if err := f.Write(out); err != nil {
		return pcount, dcount, err
	}
	log.Printf("Excel 模板完成替换: %d个参数, %d个文档", pcount, dcount)
	return pcount, dcount, nil
}

// ======================================================================
// 单元格编辑写回（对应 Python 版 update_data_cells）
// ======================================================================

type CellEdit struct {
	ParamID string `json:"param_id"` // 参数编辑时填写
	DocID   string `json:"doc_id"`   // 文档编辑时填写（文档 ID 为定位键，不可改）
	Field   string `json:"field"`    // 参数: value | description；文档: number | name
	Batch   string `json:"batch"`    // 参数 value 必填；文档编辑填批次定位单机文档，留空定位通用文档
	Value   string `json:"value"`
}

// locateDocCell 在"文档"sheet 中定位 doc_id 所在组，返回该组的 (row, colNumber, colName)。
// batch 为空时优先通用文档组，其次单机文档批次组；指定 batch 时仅在该批次组内定位。
// 行列约定与 parseExcelData 一致：第 1 行组名，第 2 行 ID/Number/Name 表头，数据从第 3 行起，每组占 3 列。
func locateDocCell(f *excelize.File, docID, batch string) (int, int, int, bool) {
	batchRe := regexp.MustCompile(`^[A-Za-z][A-Za-z0-9]*-[0-9]+$`)
	docSheet := findSheet(f.GetSheetList(), "文档")
	if docSheet == "" {
		return 0, 0, 0, false
	}
	rows, err := f.GetRows(docSheet)
	if err != nil {
		return 0, 0, 0, false
	}
	cell := func(r, c int) string {
		if r < len(rows) && c < len(rows[r]) {
			return strings.TrimSpace(rows[r][c])
		}
		return ""
	}
	colCount := 0
	for _, r := range rows {
		if len(r) > colCount {
			colCount = len(r)
		}
	}

	type group struct {
		startCol int
		name     string
		isBatch  bool
	}
	var groups []group
	for c := 0; c+2 < colCount; {
		if cell(1, c) == "ID" { // 0 基第 1 行 = 表头行
			name := cell(0, c)
			groups = append(groups, group{c, name, batchRe.MatchString(name)})
			c += 3
		} else {
			c++
		}
	}

	type target struct {
		row, colNumber, colName int
		group                   string
		isBatch                 bool
	}
	var targets []target
	for _, g := range groups {
		for r := 2; r < len(rows); r++ { // 0 基第 2 行 = 数据起始
			if cell(r, g.startCol) == docID {
				targets = append(targets, target{
					row: r + 1, colNumber: g.startCol + 2, colName: g.startCol + 3,
					group: g.name, isBatch: g.isBatch,
				})
			}
		}
	}
	if len(targets) == 0 {
		return 0, 0, 0, false
	}
	if batch != "" {
		for _, t := range targets {
			if t.group == batch {
				return t.row, t.colNumber, t.colName, true
			}
		}
		return 0, 0, 0, false
	}
	for _, t := range targets {
		if !t.isBatch {
			return t.row, t.colNumber, t.colName, true
		}
	}
	t := targets[0]
	return t.row, t.colNumber, t.colName, true
}

// updateDataCells 行列约定与 parseExcelData 一致：第 1 行批次表头（D 列起）、B 列参数 ID；
// 文档编辑写回"文档"sheet（第 1 行组名、第 2 行表头、数据从第 3 行起，每组占 3 列）
func updateDataCells(content []byte, edits []CellEdit, out io.Writer) (int, []string, error) {
	f, err := excelize.OpenReader(bytes.NewReader(content))
	if err != nil {
		return 0, nil, fmt.Errorf("解析Excel文件失败: %v", err)
	}
	defer f.Close()

	sheet := findSheet(f.GetSheetList(), "数据")
	if sheet == "" {
		return 0, nil, fmt.Errorf("未找到'数据'sheet")
	}

	// 批次列映射（第 1 行，从第 4 列/D 列起）
	batchCols := map[string]int{}
	rows, _ := f.GetRows(sheet)
	if len(rows) > 0 {
		for c := 3; c < len(rows[0]); c++ {
			name := strings.TrimSpace(rows[0][c])
			if name != "" {
				if _, exists := batchCols[name]; !exists {
					batchCols[name] = c + 1
				}
			}
		}
	}

	// 参数行映射（B 列，第 2 行为 'ID' 表头，参数从第 3 行起）
	paramRows := map[string]int{}
	if len(rows) > 1 {
		for r := 1; r < len(rows); r++ {
			id := ""
			if len(rows[r]) > 1 {
				id = strings.TrimSpace(rows[r][1])
			}
			if id != "" && id != "ID" {
				if _, exists := paramRows[id]; !exists {
					paramRows[id] = r + 1
				}
			}
		}
	}

	updated, missing := 0, []string{}
	for _, e := range edits {
		// 文档编辑：Number / Name（文档 ID 为定位键，不可改）
		if e.DocID != "" {
			if e.Field != "number" && e.Field != "name" {
				missing = append(missing, fmt.Sprintf("%s(%s)", e.DocID, e.Field))
				continue
			}
			row, colNumber, colName, found := locateDocCell(f, e.DocID, e.Batch)
			if !found {
				label := e.Batch
				if label == "" {
					label = "通用"
				}
				missing = append(missing, fmt.Sprintf("%s@%s", e.DocID, label))
				continue
			}
			docSheet := findSheet(f.GetSheetList(), "文档")
			col := colNumber
			if e.Field == "name" {
				col = colName
			}
			cell, _ := excelize.CoordinatesToCellName(col, row)
			_ = f.SetCellValue(docSheet, cell, e.Value)
			updated++
			continue
		}

		row, ok := paramRows[e.ParamID]
		if !ok {
			missing = append(missing, fmt.Sprintf("%s(%s)", e.ParamID, e.Field))
			continue
		}
		var col int
		if e.Field == "description" {
			col = 3
		} else if e.Field == "value" {
			col = batchCols[e.Batch]
			if col == 0 {
				missing = append(missing, fmt.Sprintf("%s@%s", e.ParamID, e.Batch))
				continue
			}
		} else {
			missing = append(missing, fmt.Sprintf("%s(%s)", e.ParamID, e.Field))
			continue
		}
		cell, _ := excelize.CoordinatesToCellName(col, row)

		// 原单元格是数字时尽量按数字写回，保持类型一致
		newValue := e.Value
		if origType, _ := f.GetCellType(sheet, cell); origType == excelize.CellTypeNumber {
			if fv, perr := strconv.ParseFloat(strings.TrimSpace(newValue), 64); perr == nil {
				if math.Trunc(fv) == fv && !strings.ContainsAny(strings.TrimSpace(newValue), ".eE") {
					_ = f.SetCellValue(sheet, cell, int64(fv))
				} else {
					_ = f.SetCellValue(sheet, cell, fv)
				}
				updated++
				continue
			}
		}
		_ = f.SetCellValue(sheet, cell, newValue)
		updated++
	}

	if err := f.Write(out); err != nil {
		return updated, missing, err
	}
	log.Printf("数据包单元格写回: %d 处更新, %d 处未定位", updated, len(missing))
	return updated, missing, nil
}

// filepath2Ext 小写扩展名（含点）
func filepath2Ext(name string) string {
	i := strings.LastIndex(name, ".")
	if i < 0 {
		return ""
	}
	return strings.ToLower(name[i:])
}

// isIdentifier 判断参数 ID 能否直接用作 Word 模板变量名（对应 _is_valid_jinja_identifier）
