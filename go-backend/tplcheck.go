package main

import (
	"archive/zip"
	"bytes"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/xuri/excelize/v2"
)

// ======================================================================
// 模板占位符校验（上传时与手动校验共用）
// 扫描模板中的 {{...}} 占位符，按 参数 / 文档引用 / doc() 调用 / 未匹配 分类
// ======================================================================

type PlaceholderInfo struct {
	Name string `json:"name"`
	Kind string `json:"kind"` // param: 参数 | doc: 文档引用 | doccall: doc() 调用 | unknown: 未匹配
}

// hasDocumentAny 占位符是否对应任意批次/通用文档中的文档（忽略批次归属）
func hasDocumentAny(d *DocData, placeholder string) bool {
	prefixes := getDocTypePrefixes(d)
	for _, p := range prefixes {
		re, err := regexp.Compile("^(" + regexp.QuoteMeta(p) + ")_(\\d+)$")
		if err != nil {
			continue
		}
		if m := re.FindStringSubmatch(placeholder); m != nil {
			fullID := m[1] + "_" + m[2]
			if _, ok := d.Generic[fullID]; ok {
				return true
			}
			for _, docs := range d.BatchDocs {
				if _, ok := docs[fullID]; ok {
					return true
				}
			}
			return false
		}
	}
	return false
}

// scanDocxPlaceholders 从 docx 的 XML 部件提取唯一占位符名（跨 run 归一化后）
func scanDocxPlaceholders(templatePath string) ([]string, error) {
	r, err := zip.OpenReader(templatePath)
	if err != nil {
		return nil, err
	}
	defer r.Close()

	seen := map[string]bool{}
	var names []string
	for _, f := range r.File {
		if !(f.Name == "word/document.xml" ||
			strings.HasPrefix(f.Name, "word/header") ||
			strings.HasPrefix(f.Name, "word/footer") ||
			strings.HasPrefix(f.Name, "word/footnotes") ||
			strings.HasPrefix(f.Name, "word/endnotes")) {
			continue
		}
		rc, e := f.Open()
		if e != nil {
			return nil, e
		}
		buf := new(bytes.Buffer)
		_, e = buf.ReadFrom(rc)
		rc.Close()
		if e != nil {
			return nil, e
		}
		normalized := normalizeWordXML(buf.String())
		for _, m := range placeholderGoRe.FindAllStringSubmatch(normalized, -1) {
			name := strings.TrimSpace(m[1])
			if !seen[name] {
				seen[name] = true
				names = append(names, name)
			}
		}
	}
	sort.Strings(names)
	return names, nil
}

// scanXlsxPlaceholders 从 xlsx 全部 sheet 的单元格提取唯一占位符名
func scanXlsxPlaceholders(templatePath string) ([]string, error) {
	f, err := excelize.OpenFile(templatePath)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	seen := map[string]bool{}
	var names []string
	for _, sheet := range f.GetSheetList() {
		rows, err := f.GetRows(sheet)
		if err != nil {
			continue
		}
		for _, row := range rows {
			for _, v := range row {
				if !strings.Contains(v, "{{") {
					continue
				}
				for _, m := range placeholderGoRe.FindAllStringSubmatch(v, -1) {
					name := strings.TrimSpace(m[1])
					if !seen[name] {
						seen[name] = true
						names = append(names, name)
					}
				}
			}
		}
	}
	sort.Strings(names)
	return names, nil
}

// checkTemplatePlaceholders 校验模板占位符：分类并统计未匹配项
func checkTemplatePlaceholders(templatePath string, parsed *ParsedData) (map[string]interface{}, error) {
	ext := strings.ToLower(filepath2Ext(templatePath))
	var names []string
	var err error
	if ext == ".xlsx" {
		names, err = scanXlsxPlaceholders(templatePath)
	} else {
		names, err = scanDocxPlaceholders(templatePath)
	}
	if err != nil {
		return nil, err
	}

	paramIDs := map[string]bool{}
	for _, p := range parsed.Parameters {
		paramIDs[p.ID] = true
	}

	kindLabel := map[string]string{}
	infos := make([]PlaceholderInfo, 0, len(names))
	matched, unknownCount := 0, 0
	var unmatched []string
	for _, name := range names {
		kind := "unknown"
		if paramIDs[name] {
			kind = "param"
			matched++
		} else if dm := docCallRe.FindStringSubmatch(name); dm != nil {
			if hasDocumentAny(parsed.Documents, dm[1]) {
				kind = "doccall"
				matched++
			} else {
				unknownCount++
				unmatched = append(unmatched, name)
			}
		} else if hasDocumentAny(parsed.Documents, name) {
			kind = "doc"
			matched++
		} else {
			unknownCount++
			unmatched = append(unmatched, name)
		}
		infos = append(infos, PlaceholderInfo{Name: name, Kind: kind})
		kindLabel[name] = kind
	}
	_ = kindLabel
	return map[string]interface{}{
		"total":        len(names),
		"matched":      matched,
		"unmatched":    unmatched,
		"unknownCount": unknownCount,
		"placeholders": infos,
	}, nil
}

// unwrapCheck 从 checkTemplatePlaceholders 的结果取未匹配列表
func unwrapCheck(res map[string]interface{}, err error) ([]string, error) {
	if err != nil {
		return nil, err
	}
	if raw, ok := res["unmatched"].([]string); ok {
		return raw, nil
	}
	return nil, fmt.Errorf("校验结果格式异常")
}
