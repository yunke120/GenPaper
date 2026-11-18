package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const version = "1.1.5"

func timeNow() time.Time { return time.Now() }

func formatTimestamp(t time.Time) string {
	return t.Format("2006-01-02 15:04:05")
}

func containsControlChars(s string) bool {
	for _, r := range s {
		if r < 0x20 {
			return true
		}
	}
	return false
}

// ======================================================================
// JSON 响应工具
// ======================================================================

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg interface{}) {
	writeJSON(w, status, map[string]interface{}{"error": msg})
}

// ======================================================================
// 通用类型与工具函数（对应 Python 版 app.py / report_generator.py 中的公共部分）
// ======================================================================

var windowsReservedNames = func() map[string]bool {
	m := map[string]bool{"CON": true, "PRN": true, "AUX": true, "NUL": true}
	for i := 1; i <= 9; i++ {
		m[fmt.Sprintf("COM%d", i)] = true
		m[fmt.Sprintf("LPT%d", i)] = true
	}
	return m
}()

// FileInfo 对应 Python 版 _file_info
type FileInfo struct {
	Name  string `json:"name"`
	Size  int64  `json:"size"`
	Mtime string `json:"mtime"`
}

func fileInfo(path string) FileInfo {
	st, err := os.Stat(path)
	if err != nil {
		return FileInfo{Name: filepath.Base(path)}
	}
	return FileInfo{
		Name:  filepath.Base(path),
		Size:  st.Size(),
		Mtime: st.ModTime().Format("2006-01-02 15:04:05"),
	}
}

// getDataRoot 型号数据库根目录（数据包/<型号>/），GENPAPER_DATA_ROOT 可覆盖。
// 依次探测: 环境变量 → exe 同级 → 工作目录 → 工作目录上级。
func getDataRoot() string {
	if env := os.Getenv("GENPAPER_DATA_ROOT"); env != "" {
		if abs, err := filepath.Abs(env); err == nil {
			return abs
		}
		return env
	}
	var candidates []string
	if exe, err := os.Executable(); err == nil {
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), "数据包"))
	}
	if wd, err := os.Getwd(); err == nil {
		candidates = append(candidates,
			filepath.Join(wd, "数据包"),
			filepath.Join(wd, "..", "数据包"),
		)
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && st.IsDir() {
			return c
		}
	}
	if len(candidates) > 0 {
		return candidates[0]
	}
	return "数据包"
}

// getDistDir 前端静态目录（web-app/frontend/dist）
func getDistDir() string {
	if env := os.Getenv("GENPAPER_DIST_DIR"); env != "" {
		return env
	}
	var candidates []string
	if exe, err := os.Executable(); err == nil {
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), "web-app", "frontend", "dist"))
	}
	if wd, err := os.Getwd(); err == nil {
		candidates = append(candidates,
			filepath.Join(wd, "web-app", "frontend", "dist"),
			filepath.Join(wd, "..", "web-app", "frontend", "dist"),
		)
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && st.IsDir() {
			return c
		}
	}
	return "web-app/frontend/dist"
}

// validateModelName 校验型号名称（对应 _validate_model_name）
func validateModelName(model string) (string, error) {
	model = strings.TrimSpace(model)
	if model == "" || model == "." || model == ".." {
		return "", fmt.Errorf("型号名称不能为空")
	}
	if len(model) > 80 || strings.ContainsAny(model, `\/:*?"<>|'`) || containsControlChars(model) {
		return "", fmt.Errorf("型号名称不合法: %q", model)
	}
	if windowsReservedNames[strings.ToUpper(strings.Split(model, ".")[0])] {
		return "", fmt.Errorf("型号名称不能为 Windows 保留名: %s", model)
	}
	return model, nil
}

// modelRoot 对应 _model_root
func modelRoot(model string, mustExist bool) (string, error) {
	model, err := validateModelName(model)
	if err != nil {
		return "", err
	}
	root := filepath.Join(getDataRoot(), model)
	if mustExist {
		st, err := os.Stat(root)
		if err != nil || !st.IsDir() {
			return "", fmt.Errorf("型号 %q 不存在", model)
		}
	}
	return root, nil
}

func modelTemplatesDir(model string) (string, error) {
	root, err := modelRoot(model, true)
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "templates"), nil
}

func modelVersionsDir(model string) (string, error) {
	root, err := modelRoot(model, true)
	if err != nil {
		return "", err
	}
	return filepath.Join(root, "versions"), nil
}

// currentDataFile 型号根下唯一的 .xlsx 数据包（对应 _current_data_file）
func currentDataFile(model string) (string, error) {
	root, err := modelRoot(model, true)
	if err != nil {
		return "", err
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return "", err
	}
	var found []string
	for _, e := range entries {
		if !e.IsDir() && strings.EqualFold(filepath.Ext(e.Name()), ".xlsx") {
			found = append(found, e.Name())
		}
	}
	if len(found) == 0 {
		return "", nil
	}
	if len(found) > 1 {
		return "", fmt.Errorf("型号 %q 根目录下存在多个 .xlsx 文件，请仅保留一个: %s", model, strings.Join(found, ", "))
	}
	return filepath.Join(root, found[0]), nil
}

// ======================================================================
// 文档数据模型（对应 Python 版 parse_excel_data 的返回结构）
// ======================================================================

type DocItem struct {
	Number string `json:"number"`
	Name   string `json:"name"`
}

type DocData struct {
	Generic       map[string]DocItem            // 通用文档: docID -> item
	BatchDocs     map[string]map[string]DocItem // 单机文档: batch -> docID -> item
	BatchPrefixes map[string]bool               // 单机文档前缀（动态收集，无元数据时回落 {"Doc"}）
}

type Param struct {
	ID          string
	Description string
	Data        map[string]string // batch -> value
}

type ParsedData struct {
	BatchNumbers []string
	Parameters   []*Param
	Documents    *DocData
}

var docIDRe = regexp.MustCompile(`_([0-9]+)$`)

// getDocTypePrefixes 从文档数据中动态提取所有文档类型前缀（对应 get_doc_type_prefixes）
func getDocTypePrefixes(d *DocData) []string {
	set := map[string]bool{}
	for key := range d.Generic {
		if docIDRe.MatchString(key) && strings.Contains(key, "_") {
			set[key[:strings.LastIndex(key, "_")]] = true
		}
	}
	for _, docs := range d.BatchDocs {
		for key := range docs {
			if docIDRe.MatchString(key) && strings.Contains(key, "_") {
				set[key[:strings.LastIndex(key, "_")]] = true
			}
		}
	}
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool { return len(out[i]) > len(out[j]) })
	return out
}

// getDocumentValue 对应 get_document_value；未命中返回空串
func getDocumentValue(d *DocData, placeholder, batch string) string {
	prefixes := getDocTypePrefixes(d)
	if len(prefixes) == 0 {
		return ""
	}
	var docType, docID string
	for _, p := range prefixes { // 前缀已按长度降序，最长优先
		re, err := regexp.Compile("^(" + regexp.QuoteMeta(p) + ")_(\\d+)$")
		if err != nil {
			continue
		}
		if m := re.FindStringSubmatch(placeholder); m != nil {
			docType, docID = m[1], m[2]
			break
		}
	}
	if docType == "" {
		return ""
	}
	fullID := docType + "_" + docID

	batchPrefixes := d.BatchPrefixes
	if len(batchPrefixes) == 0 {
		batchPrefixes = map[string]bool{"Doc": true}
	}
	if batchPrefixes[docType] {
		if docs, ok := d.BatchDocs[batch]; ok {
			if item, ok := docs[fullID]; ok {
				return item.Number + "《" + item.Name + "》"
			}
		}
	}
	if item, ok := d.Generic[fullID]; ok {
		return item.Number + "《" + item.Name + "》"
	}
	return ""
}

var docNameRe = regexp.MustCompile(`《(.+)》`)

// getDocumentName 对应 get_document_name：取《...》内名称作为报告保存文件名
func getDocumentName(d *DocData, placeholder, batch string) string {
	value := getDocumentValue(d, placeholder, batch)
	if value == "" {
		return ""
	}
	if m := docNameRe.FindStringSubmatch(value); m != nil {
		return m[1]
	}
	return value
}

// buildSafeOutputFilename 对应 build_safe_output_filename
func buildSafeOutputFilename(resolvedName, batchNumber, outputExt string) string {
	defaultName := fmt.Sprintf("数据报告_%s_%s%s", batchNumber, timeNow().Format("20060102_150405"), outputExt)
	if resolvedName == "" {
		return defaultName
	}
	re := regexp.MustCompile(`[<>:"/\\|?*\r\n\t]`)
	safe := strings.TrimRight(strings.TrimSpace(re.ReplaceAllString(resolvedName, "_")), ".")
	if safe != "" {
		parts := strings.SplitN(safe, ".", 2)
		if windowsReservedNames[strings.ToUpper(parts[0])] {
			safe = "_" + safe
		}
		return safe + outputExt
	}
	return defaultName
}
