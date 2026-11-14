package main

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"log"
	"regexp"
	"sort"
	"strings"
)

// ======================================================================
// Word 模板渲染（纯 ZIP/XML 层操作，对应 Python 版 _normalize_xml_for_jinja +
// _replace_in_xml_part + 文本框兜底路径；Go 版对 document.xml 统一走该路径）
// ======================================================================

var (
	placeholderGoRe  = regexp.MustCompile(`\{\{([^{}]*)\}\}`)
	runBoundaryRe    = regexp.MustCompile(`(?s)</w:t>.*?(<w:t>|<w:t [^>]*>)`)
	textNodeRe       = regexp.MustCompile(`(?s)(<w:t(?: [^>]*)?>)(.*?)(</w:t>)`)
	xmlSpecialEscape = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")
)

func xmlEscapeText(v string) string {
	return xmlSpecialEscape.Replace(v)
}

// joinSplitDelimiters 对应 _normalize_xml_for_jinja 规则一：
// 删除将 Jinja 定界符从中间隔开的标签序列（如 `{<w:t>{` → `{{`、`%}<w:t>}` → `%}`）
func joinSplitDelimiters(xml string) string {
	var b strings.Builder
	prev := byte(0)
	i := 0
	for i < len(xml) {
		c := xml[i]
		if c == '<' {
			j := skipTagRun(xml, i)
			if j > 0 && j < len(xml) {
				n := xml[j]
				if (prev == '{' && (n == '{' || n == '%' || n == '#')) ||
					((prev == '%' || prev == '}' || prev == '#') && n == '}') {
					i = j // 整段标签序列丢弃
					continue
				}
			}
		}
		b.WriteByte(c)
		prev = c
		i++
	}
	return b.String()
}

// skipTagRun i 指向 '<' 时贪婪消费连续的 <...> 标签；非标签或无闭合返回 -1
func skipTagRun(s string, i int) int {
	j := i
	for j < len(s) && s[j] == '<' {
		end := strings.IndexByte(s[j:], '>')
		if end < 0 {
			return -1
		}
		j += end + 1
	}
	if j == i {
		return -1
	}
	return j
}

type spanKind struct {
	delim string
	close string
}

var spanKinds = []spanKind{{"{{", "}}"}, {"{%", "%}"}, {"{#", "#}"}}

// stripTagsInSpans 对应规则二：删除 {{ }} / {% %} / {# #} 跨度内相邻 run 的边界
// （</w:t>...<w:t>），使占位符拼接进单一文本节点
func stripTagsInSpans(xml string) string {
	var b strings.Builder
	i := 0
	for i < len(xml) {
		best, kind := -1, 0
		for ki, k := range spanKinds {
			if idx := strings.Index(xml[i:], k.delim); idx >= 0 && (best < 0 || i+idx < best) {
				best, kind = i+idx, ki
			}
		}
		if best < 0 {
			b.WriteString(xml[i:])
			break
		}
		b.WriteString(xml[i:best])
		k := spanKinds[kind]
		rel := strings.Index(xml[best+len(k.delim):], k.close)
		if rel < 0 {
			b.WriteString(xml[best:])
			break
		}
		end := best + len(k.delim) + rel + len(k.close)
		b.WriteString(runBoundaryRe.ReplaceAllString(xml[best:end], ""))
		i = end
	}
	return b.String()
}

// normalizeWordXML 组合归一化，使跨 run 占位符可被直接匹配替换
func normalizeWordXML(xml string) string {
	return stripTagsInSpans(joinSplitDelimiters(xml))
}

// convertNewlinesInTextNodes 对应 _convert_newlines_in_text_nodes：<w:t> 内换行转 <w:br/>
func convertNewlinesInTextNodes(xml string) string {
	return textNodeRe.ReplaceAllStringFunc(xml, func(m string) string {
		g := textNodeRe.FindStringSubmatch(m)
		inner := strings.ReplaceAll(g[2], "\n", `</w:t><w:br/><w:t xml:space="preserve">`)
		return g[1] + inner + g[3]
	})
}

// resolvePlaceholder 单个占位符解析：参数 → 文档 → doc() 调用；未命中返回空串（与
// docxtpl 对未定义变量的渲染结果一致）并计入 missing
func resolvePlaceholder(name, batch string, paramContext map[string]string, d *DocData, missing *[]string) string {
	name = strings.TrimSpace(name)
	if v, ok := paramContext[name]; ok {
		return xmlEscapeText(v)
	}
	if m := docCallRe.FindStringSubmatch(name); m != nil {
		if v := getDocumentValue(d, m[1], batch); v != "" {
			return xmlEscapeText(v)
		}
		log.Printf("未找到文档数据 {{{{%s}}}}", name)
		return fmt.Sprintf("[文档缺失: %s]", m[1])
	}
	if v := getDocumentValue(d, name, batch); v != "" {
		return xmlEscapeText(v)
	}
	*missing = append(*missing, name)
	return ""
}

// countPlaceholders 统计模板部件中占位符的真实替换数（对应 _count_template_placeholders）
func countPlaceholders(parts map[string]string, paramIDs map[string]bool, d *DocData, batch string) (int, int, []string) {
	pHits, dHits := 0, 0
	var allMissing []string
	for _, xml := range parts {
		normalized := normalizeWordXML(xml)
		for _, m := range placeholderGoRe.FindAllStringSubmatch(normalized, -1) {
			name := strings.TrimSpace(m[1])
			if paramIDs[name] {
				pHits++
				continue
			}
			if dm := docCallRe.FindStringSubmatch(name); dm != nil {
				if getDocumentValue(d, dm[1], batch) != "" {
					dHits++
				} else {
					allMissing = append(allMissing, name)
				}
				continue
			}
			if getDocumentValue(d, name, batch) != "" {
				dHits++
			} else {
				allMissing = append(allMissing, name)
			}
		}
	}
	return pHits, dHits, allMissing
}

// generateWordReport 对应 generate_report 的 Word 路径：读取模板各 XML 部件，
// 归一化后替换占位符（含文本框、页眉页脚、脚注尾注），重打包输出
func generateWordReport(templatePath, batch string, params []*Param, d *DocData, out io.Writer, templateName string) (int, int, []string, error) {
	r, err := zip.OpenReader(templatePath)
	if err != nil {
		return 0, 0, nil, err
	}
	defer r.Close()

	paramContext := map[string]string{}
	for _, p := range params {
		v, ok := p.Data[batch]
		if !ok {
			v = fmt.Sprintf("[数据缺失: %s]", batch)
		}
		paramContext[p.ID] = v
	}
	paramIDs := map[string]bool{}
	for _, p := range params {
		paramIDs[p.ID] = true
	}

	// 收集需要替换的 XML 部件（正文 + 页眉页脚 + 脚注尾注）
	parts := map[string]string{}
	files := map[string]*zip.File{}
	var entryNames []string
	for _, f := range r.File {
		files[f.Name] = f
		entryNames = append(entryNames, f.Name)
	}
	sort.Strings(entryNames)
	for _, name := range entryNames {
		if name == "word/document.xml" ||
			strings.HasPrefix(name, "word/header") ||
			strings.HasPrefix(name, "word/footer") ||
			strings.HasPrefix(name, "word/footnotes") ||
			strings.HasPrefix(name, "word/endnotes") {
			rc, e := files[name].Open()
			if e != nil {
				return 0, 0, nil, e
			}
			buf := new(bytes.Buffer)
			if _, e := buf.ReadFrom(rc); e != nil {
				rc.Close()
				return 0, 0, nil, e
			}
			rc.Close()
			parts[name] = buf.String()
		}
	}

	// 统计模板中占位符的真实替换数
	pHits, dHits, missing := countPlaceholders(parts, paramIDs, d, batch)
	if len(missing) > 0 {
		uniq := map[string]bool{}
		var uniqList []string
		for _, m := range missing {
			if !uniq[m] {
				uniq[m] = true
				uniqList = append(uniqList, m)
			}
		}
		log.Printf("模板中 %d 个占位符未匹配到数据: %v", len(missing), uniqList)
	}

	// 逐部件替换（值统一做 XML 转义；渲染后 <w:t> 内换行转 <w:br/>）
	replaced := map[string]string{}
	for name, xml := range parts {
		normalized := normalizeWordXML(xml)
		result := placeholderGoRe.ReplaceAllStringFunc(normalized, func(m string) string {
			name := strings.TrimSpace(placeholderGoRe.FindStringSubmatch(m)[1])
			return resolvePlaceholder(name, batch, paramContext, d, &missing)
		})
		replaced[name] = convertNewlinesInTextNodes(result)
	}

	// 重打包：复制原 zip 全部条目，替换已修改的 XML 部件
	if err := rewriteDocxZip(templatePath, out, replaced); err != nil {
		return pHits, dHits, missing, err
	}
	log.Printf("完成替换: %d个参数, %d个文档", pHits, dHits)
	return pHits, dHits, missing, nil
}


// rewriteDocxZip 复制模板 zip 全部条目并用替换后的 XML 覆盖对应部件
func rewriteDocxZip(templatePath string, out io.Writer, replaced map[string]string) error {
	src, err := zip.OpenReader(templatePath)
	if err != nil {
		return err
	}
	defer src.Close()

	w := zip.NewWriter(out)
	for _, f := range src.File {
		data, e := readZipEntry(f)
		if e != nil {
			return e
		}
		if repl, ok := replaced[f.Name]; ok {
			data = []byte(repl)
		}
		fw, e := w.Create(f.Name)
		if e != nil {
			return e
		}
		if _, e := fw.Write(data); e != nil {
			return e
		}
	}
	return w.Close()
}

func readZipEntry(f *zip.File) ([]byte, error) {
	rc, err := f.Open()
	if err != nil {
		return nil, err
	}
	defer rc.Close()
	buf := new(bytes.Buffer)
	if _, err := buf.ReadFrom(rc); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// ======================================================================
// 数据一致性对比 Word 报告（从零构建最小 docx，对应 generate_consistency_report）
// 判定口径：字符串精确比较（strip 后）；空值不参与判定
// ======================================================================

func consistencyRow(values []string) (bool, int) {
	seen := map[string]bool{}
	for _, v := range values {
		t := strings.TrimSpace(v)
		if t != "" {
			seen[t] = true
		}
	}
	return len(seen) <= 1, len(values) - len(seen)
}

// XML 文本节点转义 + 换行转 <w:br/>
func docxText(v string) string {
	escaped := xmlEscapeText(v)
	return strings.ReplaceAll(escaped, "\n", `<w:br/>`)
}

func docxParagraph(text string, bold bool, halfPoints int, center bool) string {
	var b strings.Builder
	b.WriteString("<w:p><w:pPr>")
	if center {
		b.WriteString(`<w:jc w:val="center"/>`)
	}
	b.WriteString("</w:pPr><w:r><w:rPr>")
	if bold {
		b.WriteString("<w:b/>")
	}
	if halfPoints > 0 {
		b.WriteString(fmt.Sprintf(`<w:sz w:val="%d"/><w:szCs w:val="%d"/>`, halfPoints, halfPoints))
	}
	b.WriteString("</w:rPr>")
	b.WriteString(`<w:t xml:space="preserve">` + docxText(text) + `</w:t>`)
	b.WriteString("</w:r></w:p>")
	return b.String()
}

func docxCell(text string, bold bool, halfPoints int) string {
	return "<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>" +
		docxParagraph(text, bold, halfPoints, false) +
		"</w:tc>"
}

func buildConsistencyDocx(out io.Writer, batches []string, params []*Param) error {
	var body strings.Builder

	body.WriteString(docxParagraph("数据一致性对比", true, 32, true))
	body.WriteString(docxParagraph(fmt.Sprintf("对比批次：%s", strings.Join(batches, "、")), false, 0, false))
	body.WriteString(docxParagraph(fmt.Sprintf("对比参数：%d 项", len(params)), false, 0, false))

	tblBorders := "<w:tblBorders>" +
		`<w:top w:val="single" w:sz="4" w:color="000000"/>` +
		`<w:left w:val="single" w:sz="4" w:color="000000"/>` +
		`<w:bottom w:val="single" w:sz="4" w:color="000000"/>` +
		`<w:right w:val="single" w:sz="4" w:color="000000"/>` +
		`<w:insideH w:val="single" w:sz="4" w:color="000000"/>` +
		`<w:insideV w:val="single" w:sz="4" w:color="000000"/>` +
		"</w:tblBorders>"

	body.WriteString("<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/>" + tblBorders + "</w:tblPr>")

	// 表头
	body.WriteString("<w:tr>")
	body.WriteString(docxCell("序号", true, 18))
	body.WriteString(docxCell("参数ID", true, 18))
	body.WriteString(docxCell("Description", true, 18))
	for _, b := range batches {
		body.WriteString(docxCell(b, true, 18))
	}
	body.WriteString(docxCell("一致性", true, 18))
	body.WriteString("</w:tr>")

	// 数据行
	inconsistentCount, missingValues := 0, 0
	for idx, p := range params {
		values := make([]string, 0, len(batches))
		for _, b := range batches {
			values = append(values, p.Data[b])
		}
		consistent, missing := consistencyRow(values)
		missingValues += missing
		if !consistent {
			inconsistentCount++
		}
		body.WriteString("<w:tr>")
		body.WriteString(docxCell(fmt.Sprintf("%d", idx+1), false, 18))
		body.WriteString(docxCell(p.ID, false, 18))
		body.WriteString(docxCell(p.Description, false, 18))
		for _, v := range values {
			body.WriteString(docxCell(v, false, 18))
		}
		verdict := "一致"
		if !consistent {
			verdict = "不一致"
		}
		body.WriteString(docxCell(verdict, false, 18))
		body.WriteString("</w:tr>")
	}
	body.WriteString("</w:tbl>")

	summary := fmt.Sprintf("共对比 %d 个参数、%d 个批次；一致 %d 项，不一致 %d 项。",
		len(params), len(batches), len(params)-inconsistentCount, inconsistentCount)
	if missingValues > 0 {
		summary += fmt.Sprintf("其中 %d 个对比值为空（空值不参与一致性判定）。", missingValues)
	}
	body.WriteString(docxParagraph(summary, false, 0, false))

	document := "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" +
		`<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>` +
		body.String() +
		`<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800" w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>` +
		`</w:body></w:document>`

	contentTypes := `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
		`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
		`<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
		`<Default Extension="xml" ContentType="application/xml"/>` +
		`<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>` +
		`<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>` +
		`</Types>`

	rels := `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
		`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
		`<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>` +
		`</Relationships>`

	docRels := `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
		`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
		`<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>` +
		`</Relationships>`

	styles := `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>` +
		`<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">` +
		`<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:eastAsia="宋体" w:hAnsi="Calibri"/>` +
		`<w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>` +
		`<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>` +
		`</w:styles>`

	files := []struct{ Name, Content string }{
		{"[Content_Types].xml", contentTypes},
		{"_rels/.rels", rels},
		{"word/document.xml", document},
		{"word/_rels/document.xml.rels", docRels},
		{"word/styles.xml", styles},
	}

	w := zip.NewWriter(out)
	for _, f := range files {
		fw, err := w.Create(f.Name)
		if err != nil {
			return err
		}
		if _, err := fw.Write([]byte(f.Content)); err != nil {
			return err
		}
	}
	log.Printf("一致性报告完成: %d个参数, %d个批次, %d项不一致", len(params), len(batches), inconsistentCount)
	return w.Close()
}
