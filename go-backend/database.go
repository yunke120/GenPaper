package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
)

// ======================================================================
// 解析缓存（对应 Python 版 _parse_cache：按 mtime 失效）
// ======================================================================

type parseCacheEntry struct {
	mtime int64
	data  *ParsedData
}

var (
	parseCacheMu sync.Mutex
	parseCache   = map[string]parseCacheEntry{}
)

func parseCachePop(model string) {
	parseCacheMu.Lock()
	delete(parseCache, model)
	parseCacheMu.Unlock()
}

func getParsedData(model string) (*ParsedData, error) {
	path, err := currentDataFile(model)
	if err != nil {
		return nil, err
	}
	if path == "" {
		return nil, nil
	}
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	mtime := st.ModTime().UnixNano()

	parseCacheMu.Lock()
	if e, ok := parseCache[model]; ok && e.mtime == mtime {
		parseCacheMu.Unlock()
		return e.data, nil
	}
	parseCacheMu.Unlock()

	data, err := parseExcelData(path)
	if err != nil {
		return nil, err
	}
	parseCacheMu.Lock()
	if len(parseCache) >= 8 {
		for k := range parseCache { // 简单驱逐：超出容量时删除任一旧条目
			delete(parseCache, k)
			break
		}
	}
	parseCache[model] = parseCacheEntry{mtime: mtime, data: data}
	parseCacheMu.Unlock()
	return data, nil
}

// ======================================================================
// 模板列表与报告类型配置（对应 _model_templates / templates.json）
// ======================================================================

type Template struct {
	Name       string `json:"name"`
	ReportType string `json:"report_type"`
	Size       int64  `json:"size"`
	Mtime      string `json:"mtime"`
	Unmatched  int    `json:"unmatched"` // 未匹配占位符数（-1 = 无数据包无法校验）
}

const templateConfigFilename = "templates.json"

func templateConfigPath(model string) (string, error) {
	dir, err := modelTemplatesDir(model)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, templateConfigFilename), nil
}

func readTemplateConfig(model string) map[string]string {
	path, err := templateConfigPath(model)
	if err != nil {
		return map[string]string{}
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]string{}
	}
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return map[string]string{}
	}
	out := map[string]string{}
	for k, v := range raw {
		ks, vs := strings.TrimSpace(fmt.Sprintf("%v", k)), strings.TrimSpace(fmt.Sprintf("%v", v))
		if ks != "" && vs != "" {
			out[ks] = vs
		}
	}
	return out
}

func writeTemplateConfig(model string, mapping map[string]string) error {
	path, err := templateConfigPath(model)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	data, _ := json.MarshalIndent(mapping, "", "  ")
	return os.WriteFile(path, data, 0o644)
}

func modelTemplates(model string) ([]Template, error) {
	dir, err := modelTemplatesDir(model)
	if err != nil {
		return nil, err
	}
	config := readTemplateConfig(model)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return []Template{}, nil
	}
	var out []Template
	for _, e := range entries {
		if e.IsDir() || strings.HasPrefix(e.Name(), "~$") {
			continue
		}
		ext := strings.ToLower(filepath.Ext(e.Name()))
		if ext != ".docx" && ext != ".xlsx" {
			continue
		}
		info := fileInfo(filepath.Join(dir, e.Name()))
		stem := strings.TrimSuffix(e.Name(), filepath.Ext(e.Name()))
		rt := config[e.Name()]
		if rt == "" {
			rt = config[stem]
		}
		out = append(out, Template{Name: e.Name(), ReportType: rt, Size: info.Size, Mtime: info.Mtime})
	}
	return out, nil
}

// ======================================================================
// 版本管理（对应 _snapshot_current / _list_versions / _version_path）
// ======================================================================

const versionFilePrefix = "数据包_"

type Version struct {
	ID       string `json:"id"`
	Filename string `json:"filename"`
	Size     int64  `json:"size"`
	Time     string `json:"time"`
	Note     string `json:"note"`
	Source   string `json:"source"` // auto: 变更前自动快照 / upload: 上传 / edit: 表格编辑
}

func readVersionsMeta(model string) (map[string]map[string]interface{}, error) {
	dir, err := modelVersionsDir(model)
	if err != nil {
		return nil, err
	}
	data, err := os.ReadFile(filepath.Join(dir, "meta.json"))
	if err != nil {
		return map[string]map[string]interface{}{}, nil
	}
	out := map[string]map[string]interface{}{}
	if err := json.Unmarshal(data, &out); err != nil {
		return map[string]map[string]interface{}{}, nil
	}
	return out, nil
}

func writeVersionsMeta(model string, meta map[string]map[string]interface{}) error {
	dir, err := modelVersionsDir(model)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(meta); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "meta.json"), []byte(buf.String()), 0o644)
}

// snapshotCurrent 对应 _snapshot_current：无当前数据包时返回 ""
func snapshotCurrent(model, source, note string) (string, error) {
	src, err := currentDataFile(model)
	if err != nil {
		return "", err
	}
	if src == "" {
		return "", nil
	}
	dir, err := modelVersionsDir(model)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	stamp := timeNow().Format("20060102_150405")
	vid, seq := stamp, 1
	for fileExists(filepath.Join(dir, versionFilePrefix+vid+".xlsx")) {
		seq++
		vid = fmt.Sprintf("%s_%d", stamp, seq)
	}
	dst := filepath.Join(dir, versionFilePrefix+vid+".xlsx")
	if err := copyFile(src, dst); err != nil {
		return "", err
	}
	meta, err := readVersionsMeta(model)
	if err != nil {
		meta = map[string]map[string]interface{}{}
	}
	dstInfo := fileInfo(dst)
	meta[vid] = map[string]interface{}{
		"filename": filepath.Base(src),
		"size":     dstInfo.Size,
		"time":     formatTimestamp(timeNow()),
		"note":     note,
		"source":   source,
	}
	if err := writeVersionsMeta(model, meta); err != nil {
		return "", err
	}
	log.Printf("型号 %s 数据包已记录版本 %s（来源: %s）", model, vid, source)
	return vid, nil
}

var vidRe = regexp.MustCompile(`^[\w-]+$`)

func versionPath(model, vid string) (string, error) {
	if !vidRe.MatchString(vid) {
		return "", fmt.Errorf("版本 ID 不合法")
	}
	dir, err := modelVersionsDir(model)
	if err != nil {
		return "", err
	}
	path := filepath.Join(dir, versionFilePrefix+vid+".xlsx")
	if !fileExists(path) {
		return "", fmt.Errorf("版本 %s 不存在", vid)
	}
	return path, nil
}

func listVersions(model string) ([]Version, error) {
	dir, err := modelVersionsDir(model)
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return []Version{}, nil
	}
	meta, _ := readVersionsMeta(model)
	var out []Version
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasPrefix(name, versionFilePrefix) || !strings.EqualFold(filepath.Ext(name), ".xlsx") {
			continue
		}
		vid := strings.TrimSuffix(strings.TrimPrefix(name, versionFilePrefix), ".xlsx")
		info := fileInfo(filepath.Join(dir, name))
		v := Version{ID: vid, Filename: name, Size: info.Size, Time: info.Mtime}
		if m, ok := meta[vid]; ok {
			if s, ok := m["time"].(string); ok && s != "" {
				v.Time = s
			}
			if s, ok := m["note"].(string); ok {
				v.Note = s
			}
			if s, ok := m["source"].(string); ok {
				v.Source = s
			}
		}
		out = append(out, v)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID > out[j].ID })
	return out, nil
}

// saveNewDataFile 对应 _save_new_data_file：旧版本自动快照 + 新版本入历史
func saveNewDataFile(model string, content []byte, filename, note string) (FileInfo, error) {
	if !strings.EqualFold(filepath.Ext(filename), ".xlsx") {
		return FileInfo{}, fmt.Errorf("数据包必须是 .xlsx 文件")
	}
	if err := validateXlsxContent(content); err != nil {
		return FileInfo{}, err
	}
	if _, err := snapshotCurrent(model, "auto", ""); err != nil {
		return FileInfo{}, err
	}

	root, err := modelRoot(model, true)
	if err != nil {
		return FileInfo{}, err
	}
	entries, _ := os.ReadDir(root)
	for _, e := range entries {
		if !e.IsDir() && strings.EqualFold(filepath.Ext(e.Name()), ".xlsx") {
			if err := os.Remove(filepath.Join(root, e.Name())); err != nil {
				return FileInfo{}, err
			}
		}
	}
	dst := filepath.Join(root, filepath.Base(filename))
	if err := os.WriteFile(dst, content, 0o644); err != nil {
		return FileInfo{}, err
	}
	parseCachePop(model)

	newVid, err := snapshotCurrent(model, "upload", note)
	if err != nil {
		return FileInfo{}, err
	}
	log.Printf("型号 %s 数据包已更新: %s（版本 %s）", model, filepath.Base(dst), newVid)
	return fileInfo(dst), nil
}

func validateXlsxContent(content []byte) error {
	r, err := zip.NewReader(bytes.NewReader(content), int64(len(content)))
	if err != nil {
		return fmt.Errorf("不是有效的 .xlsx 文件")
	}
	// 完整读取以校验 CRC
	for _, f := range r.File {
		rc, err := f.Open()
		if err != nil {
			return fmt.Errorf("不是有效的 .xlsx 文件")
		}
		buf := new(bytes.Buffer)
		if _, err := io.Copy(buf, rc); err != nil {
			rc.Close()
			return fmt.Errorf("文件内容损坏")
		}
		rc.Close()
	}
	return nil
}

// ======================================================================
// HTTP 处理（数据库 API）
// ======================================================================

func handleDatabaseOverview(w http.ResponseWriter, r *http.Request) {
	root := getDataRoot()
	models := []map[string]interface{}{}
	entries, err := os.ReadDir(root)
	if err == nil {
		for _, e := range entries {
			if !e.IsDir() || strings.HasPrefix(e.Name(), ".") || strings.HasPrefix(e.Name(), "~$") {
				continue
			}
			summary, err := modelSummary(e.Name())
			if err != nil {
				log.Printf("跳过无法识别的目录 %s: %v", e.Name(), err)
				continue
			}
			models = append(models, summary)
		}
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":   true,
		"data_root": filepath.Base(root),
		"models":    models,
	})
}

// modelSummary 对应 _model_summary
func modelSummary(model string) (map[string]interface{}, error) {
	dataPath, err := currentDataFile(model)
	if err != nil {
		return nil, err
	}
	hasData := dataPath != ""
	batchCount := 0
	if hasData {
		if parsed, err := getParsedData(model); err == nil && parsed != nil {
			batchCount = len(parsed.BatchNumbers)
		} else if err != nil {
			log.Printf("警告: 型号 %s 数据包解析失败: %v", model, err)
		}
	}
	tpls, _ := modelTemplates(model)
	summary := map[string]interface{}{
		"model":          model,
		"has_data":       hasData,
		"batch_count":    batchCount,
		"template_count": len(tpls),
		"data_file":      nil,
	}
	if hasData {
		summary["data_file"] = fileInfo(dataPath)
	}
	return summary, nil
}

func handleDatabaseCreateModel(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(50 << 20); err != nil {
		writeError(w, http.StatusBadRequest, "请求数据为空")
		return
	}
	model, err := validateModelName(r.FormValue("name"))
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	root := filepath.Join(getDataRoot(), model)
	if fileExists(root) {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 已存在", model))
		return
	}
	if err := os.MkdirAll(filepath.Join(root, "templates"), 0o755); err != nil {
		log.Printf("创建型号失败: %v", err)
		writeError(w, http.StatusInternalServerError, "创建型号失败，请查看服务端日志")
		return
	}
	if err := os.MkdirAll(filepath.Join(root, "versions"), 0o755); err != nil {
		log.Printf("创建型号失败: %v", err)
		writeError(w, http.StatusInternalServerError, "创建型号失败，请查看服务端日志")
		return
	}
	if file, header, err := r.FormFile("file"); err == nil && header.Filename != "" {
		defer file.Close()
		content, err := io.ReadAll(file)
		if err != nil {
			writeError(w, http.StatusInternalServerError, "读取初始数据包失败")
			return
		}
		if _, err := saveNewDataFile(model, content, header.Filename, "初始上传"); err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
	}
	log.Printf("已创建型号: %s", model)
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true, "model": model})
}

func handleDatabaseDetail(w http.ResponseWriter, r *http.Request, model string) {
	dataPath, err := currentDataFile(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	hasData := dataPath != ""
	batches := []string{}
	paramCount := 0
	docStats := map[string]int{"通用文档": 0, "单机文档": 0}
	parseError := ""
	if hasData {
		parsed, err := getParsedData(model)
		if err != nil {
			parseError = err.Error()
		} else if parsed != nil {
			batches = parsed.BatchNumbers
			paramCount = len(parsed.Parameters)
			docStats = map[string]int{
				"通用文档": len(parsed.Documents.Generic),
				"单机文档": lenBatchDocs(parsed.Documents.BatchDocs),
			}
		}
	}
	tpls, err := modelTemplates(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	// 占位符校验：有数据包时为每个模板统计未匹配占位符数（无数据 → -1）
	for i := range tpls {
		tpls[i].Unmatched = -1
	}
	if hasData {
		if parsed, perr := getParsedData(model); perr == nil && parsed != nil {
			dir, derr := modelTemplatesDir(model)
			if derr == nil {
				for i := range tpls {
					if res, cerr := checkTemplatePlaceholders(filepath.Join(dir, tpls[i].Name), parsed); cerr == nil {
						tpls[i].Unmatched = len(res["unmatched"].([]string))
					} else {
						tpls[i].Unmatched = -1
					}
				}
			}
		}
	}
	versions, _ := listVersions(model)
	detail := map[string]interface{}{
		"success":         true,
		"model":           model,
		"has_data":        hasData,
		"data_file":       nil,
		"batch_numbers":   batches,
		"parameter_count": paramCount,
		"document_stats":  docStats,
		"parse_error":     parseError,
		"templates":       tpls,
		"versions":        versions,
	}
	if hasData {
		detail["data_file"] = fileInfo(dataPath)
	}
	writeJSON(w, http.StatusOK, detail)
}

// handleDatabaseOpenFolder POST /api/database/models/<model>/open_folder
// 用系统文件管理器打开该型号的数据包文件夹（型号根目录）。
// 可选 JSON 参数 {"sub": "templates"|"versions"|""} 用于打开子目录。
// 说明：GenPaper 只监听 127.0.0.1，后端与浏览器在同一台机器上，
// 因此由后端调用系统文件管理器即可。
func handleDatabaseOpenFolder(w http.ResponseWriter, r *http.Request, model string) {
	var body struct {
		Sub string `json:"sub"`
	}
	// 允许无请求体（部分客户端不发送 JSON）
	if r.ContentLength > 0 {
		_ = decodeJSON(r, &body)
	}
	root, err := modelRoot(model, true)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	target := root
	switch strings.ToLower(strings.TrimSpace(body.Sub)) {
	case "", "root", ".":
		target = root
	case "templates":
		target = filepath.Join(root, "templates")
	case "versions":
		target = filepath.Join(root, "versions")
	default:
		writeError(w, http.StatusBadRequest, fmt.Sprintf("不支持的子目录: %s", body.Sub))
		return
	}
	if st, serr := os.Stat(target); serr != nil || !st.IsDir() {
		// 子目录缺失时回退到型号根目录，避免"打不开"的死路
		if target != root {
			log.Printf("型号 %s 子目录不存在，回退到型号根目录: %s", model, target)
			target = root
		} else {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 目录不存在", model))
			return
		}
	}
	if err := openFolderInFileManager(target); err != nil {
		log.Printf("打开文件夹失败: %s: %v", target, err)
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("打开文件夹失败: %v", err))
		return
	}
	log.Printf("型号 %s 已打开文件夹: %s", model, target)
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success": true,
		"path":    target,
	})
}

func lenBatchDocs(m map[string]map[string]DocItem) int {
	n := 0
	for _, docs := range m {
		n += len(docs)
	}
	return n
}

func handleDatabaseUploadData(w http.ResponseWriter, r *http.Request, model string) {
	if err := r.ParseMultipartForm(50 << 20); err != nil {
		writeError(w, http.StatusBadRequest, "没有文件上传")
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil || header.Filename == "" {
		writeError(w, http.StatusBadRequest, "没有文件上传")
		return
	}
	defer file.Close()
	content, err := io.ReadAll(file)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "读取文件失败")
		return
	}
	info, err := saveNewDataFile(model, content, header.Filename, strings.TrimSpace(r.FormValue("note")))
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true, "data_file": info})
}

func handleDatabaseUploadTemplate(w http.ResponseWriter, r *http.Request, model string) {
	if err := r.ParseMultipartForm(50 << 20); err != nil {
		writeError(w, http.StatusBadRequest, "没有文件上传")
		return
	}
	file, header, err := r.FormFile("file")
	if err != nil || header.Filename == "" {
		writeError(w, http.StatusBadRequest, "没有文件上传")
		return
	}
	defer file.Close()
	name := filepath.Base(header.Filename)
	ext := strings.ToLower(filepath.Ext(name))
	if ext != ".docx" && ext != ".xlsx" {
		writeError(w, http.StatusBadRequest, "模板必须是 .docx 或 .xlsx 文件")
		return
	}
	dir, err := modelTemplatesDir(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		log.Printf("上传模板失败: %v", err)
		writeError(w, http.StatusInternalServerError, "上传模板失败，请查看服务端日志")
		return
	}
	dst := filepath.Join(dir, name)
	if fileExists(dst) {
		log.Printf("型号 %s 模板覆盖: %s", model, name)
	}
	content, err := io.ReadAll(file)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "读取文件失败")
		return
	}
	if err := os.WriteFile(dst, content, 0o644); err != nil {
		log.Printf("上传模板失败: %v", err)
		writeError(w, http.StatusInternalServerError, "上传模板失败，请查看服务端日志")
		return
	}

	// 上传后立即做占位符校验（有数据包时）
	var checkResult map[string]interface{}
	if parsed, perr := getParsedData(model); perr == nil && parsed != nil {
		if res, cerr := checkTemplatePlaceholders(dst, parsed); cerr == nil {
			checkResult = res
		}
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":  true,
		"template": map[string]interface{}{"name": name, "report_type": "", "size": int64(len(content)), "mtime": formatTimestamp(timeNow())},
		"check":    checkResult,
	})
}

// handleTemplateCheck GET /templates/<name>/check：单个模板的占位符校验详情
func handleTemplateCheck(w http.ResponseWriter, r *http.Request, model, tplName string) {
	tplName = filepath.Base(tplName)
	parsed, err := getParsedData(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if parsed == nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 尚无数据包，请先上传后再校验", model))
		return
	}
	dir, err := modelTemplatesDir(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	templatePath := filepath.Join(dir, tplName)
	if !fileExists(templatePath) {
		writeError(w, http.StatusNotFound, fmt.Sprintf("模板 %q 不存在", tplName))
		return
	}
	res, err := checkTemplatePlaceholders(templatePath, parsed)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	res["success"] = true
	res["template"] = tplName
	writeJSON(w, http.StatusOK, res)
}

func handleDatabaseTemplateConfig(w http.ResponseWriter, r *http.Request, model string) {
	var body struct {
		Types map[string]string `json:"types"`
	}
	if err := decodeJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "请求数据为空")
		return
	}
	if body.Types == nil {
		writeError(w, http.StatusBadRequest, "缺少 types 映射")
		return
	}
	mapping := map[string]string{}
	for k, v := range body.Types {
		k, v = strings.TrimSpace(k), strings.TrimSpace(v)
		if k != "" && v != "" {
			mapping[k] = v
		}
	}
	existing, err := modelTemplates(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	stems := map[string]bool{}
	for _, t := range existing {
		stems[t.Name] = true
		stems[strings.TrimSuffix(t.Name, filepath.Ext(t.Name))] = true
	}
	var unknown []string
	for k := range mapping {
		if !stems[k] {
			unknown = append(unknown, k)
		}
	}
	if len(unknown) > 0 {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("以下键没有对应的模板文件: %s", strings.Join(unknown, ", ")))
		return
	}
	if err := writeTemplateConfig(model, mapping); err != nil {
		log.Printf("保存模板报告类型失败: %v", err)
		writeError(w, http.StatusInternalServerError, "保存模板报告类型失败，请查看服务端日志")
		return
	}
	log.Printf("型号 %s 模板报告类型已更新（%d 项）", model, len(mapping))
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true, "types": mapping})
}

func handleDatabaseParameters(w http.ResponseWriter, r *http.Request, model string) {
	parsed, err := getParsedData(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if parsed == nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 尚无数据包，请先上传", model))
		return
	}
	params := []map[string]interface{}{}
	for _, p := range parsed.Parameters {
		params = append(params, map[string]interface{}{
			"id":          p.ID,
			"description": p.Description,
			"values":      p.Data,
		})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":       true,
		"batch_numbers": parsed.BatchNumbers,
		"parameters":    params,
		"documents": map[string]interface{}{
			"generic":    parsed.Documents.Generic,
			"batch_docs": parsed.Documents.BatchDocs,
		},
	})
}

func handleDatabaseGenerate(w http.ResponseWriter, r *http.Request, model string) {
	var body struct {
		TemplateFilename string `json:"template_filename"`
		BatchNumber      string `json:"batch_number"`
	}
	if err := decodeJSON(r, &body); err != nil || body.TemplateFilename == "" || body.BatchNumber == "" {
		writeError(w, http.StatusBadRequest, "缺少 template_filename 或 batch_number")
		return
	}
	generateOne(w, model, body.TemplateFilename, body.BatchNumber)
}

// generateOne 单模板生成（对应 /api/database/models/<model>/generate）
func generateOne(w http.ResponseWriter, model, templateFilename, batch string) {
	parsed, err := getParsedData(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if parsed == nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 尚无数据包，请先上传", model))
		return
	}
	if !containsString(parsed.BatchNumbers, batch) {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("批次号 %q 不存在", batch))
		return
	}
	dir, err := modelTemplatesDir(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	templatePath := filepath.Join(dir, filepath.Base(templateFilename))
	if !fileExists(templatePath) {
		writeError(w, http.StatusNotFound, fmt.Sprintf("模板 %q 不存在", templateFilename))
		return
	}
	if ext := strings.ToLower(filepath.Ext(templateFilename)); ext != ".docx" && ext != ".xlsx" {
		writeError(w, http.StatusBadRequest, "模板必须是 .docx 或 .xlsx 文件")
		return
	}

	templateKind := "docx"
	outputExt := ".docx"
	if strings.EqualFold(filepath.Ext(templateFilename), ".xlsx") {
		templateKind, outputExt = "excel", ".xlsx"
	}
	stem := strings.TrimSuffix(templateFilename, filepath.Ext(templateFilename))
	outputName := buildSafeOutputFilename(getDocumentName(parsed.Documents, stem, batch), batch, outputExt)

	var buf bytes.Buffer
	var pcount, dcount int
	if templateKind == "excel" {
		pcount, dcount, err = generateExcelReport(templatePath, batch, parsed.Parameters, parsed.Documents, &buf, templateFilename)
	} else {
		pcount, dcount, _, err = generateWordReport(templatePath, batch, parsed.Parameters, parsed.Documents, &buf, templateFilename)
	}
	if err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "报告生成失败，请查看服务端日志")
		return
	}

	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":         true,
		"output_filename": outputName,
		"file_type":       templateKind,
		"report_content":  base64Encode(buf.Bytes()),
		"statistics": map[string]interface{}{
			"batch_number":           batch,
			"parameter_replacements": pcount,
			"document_replacements":  dcount,
			"total_replacements":     pcount + dcount,
		},
	})
}

func handleDatabaseGenerateAll(w http.ResponseWriter, r *http.Request, model string) {
	var body struct {
		BatchNumber string `json:"batch_number"`
	}
	if err := decodeJSON(r, &body); err != nil || body.BatchNumber == "" {
		writeError(w, http.StatusBadRequest, "缺少 batch_number")
		return
	}
	parsed, err := getParsedData(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if parsed == nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 尚无数据包，请先上传", model))
		return
	}
	if !containsString(parsed.BatchNumbers, body.BatchNumber) {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("批次号 %q 不存在", body.BatchNumber))
		return
	}
	templates, err := modelTemplates(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if len(templates) == 0 {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 暂无模板，请先上传", model))
		return
	}

	dir, err := modelTemplatesDir(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	buf := new(bytes.Buffer)
	zw := zip.NewWriter(buf)
	usedNames := map[string]bool{}
	type result struct {
		Template              string `json:"template"`
		Success               bool   `json:"success"`
		OutputFilename        string `json:"output_filename,omitempty"`
		Error                 string `json:"error,omitempty"`
		ParameterReplacements int    `json:"parameter_replacements,omitempty"`
		DocumentReplacements  int    `json:"document_replacements,omitempty"`
	}
	var results []result
	okCount := 0
	for _, t := range templates {
		templatePath := filepath.Join(dir, t.Name)
		templateKind := "docx"
		outputExt := ".docx"
		if strings.EqualFold(filepath.Ext(t.Name), ".xlsx") {
			templateKind, outputExt = "excel", ".xlsx"
		}
		stem := strings.TrimSuffix(t.Name, filepath.Ext(t.Name))
		outputName := buildSafeOutputFilename(getDocumentName(parsed.Documents, stem, body.BatchNumber), body.BatchNumber, outputExt)

		var rb bytes.Buffer
		var pcount, dcount int
		var err error
		if templateKind == "excel" {
			pcount, dcount, err = generateExcelReport(templatePath, body.BatchNumber, parsed.Parameters, parsed.Documents, &rb, t.Name)
		} else {
			pcount, dcount, _, err = generateWordReport(templatePath, body.BatchNumber, parsed.Parameters, parsed.Documents, &rb, t.Name)
		}
		if err != nil {
			results = append(results, result{Template: t.Name, Success: false, Error: "生成失败，请查看服务端日志"})
			continue
		}
		// zip 内文件名去重（不同模板可能解析出相同的文档名）
		arcname := outputName
		for seq := 2; usedNames[arcname]; seq++ {
			arcname = fmt.Sprintf("%s_%d%s", strings.TrimSuffix(outputName, outputExt), seq, outputExt)
		}
		usedNames[arcname] = true
		fw, err := zw.Create(arcname)
		if err != nil {
			log.Printf("%v", err)
			results = append(results, result{Template: t.Name, Success: false, Error: "打包失败"})
			continue
		}
		if _, err := fw.Write(rb.Bytes()); err != nil {
			log.Printf("%v", err)
			results = append(results, result{Template: t.Name, Success: false, Error: "打包失败"})
			continue
		}
		okCount++
		results = append(results, result{
			Template: t.Name, Success: true, OutputFilename: outputName,
			ParameterReplacements: pcount, DocumentReplacements: dcount,
		})
	}
	if err := zw.Close(); err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "打包失败，请查看服务端日志")
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":        okCount > 0,
		"zip_filename":   fmt.Sprintf("%s_%s_报告合集_%s.zip", model, body.BatchNumber, timeNow().Format("20060102_150405")),
		"report_content": base64Encode(buf.Bytes()),
		"results":        results,
		"statistics": map[string]interface{}{
			"total_templates": len(templates),
			"success_count":   okCount,
			"failed_count":    len(results) - okCount,
		},
	})
}

func handleDatabaseUpdateData(w http.ResponseWriter, r *http.Request, model string) {
	var body struct {
		Edits []CellEdit `json:"edits"`
		Note  string     `json:"note"`
	}
	if err := decodeJSON(r, &body); err != nil || len(body.Edits) == 0 {
		writeError(w, http.StatusBadRequest, "缺少编辑内容")
		return
	}
	clean := []CellEdit{}
	for _, e := range body.Edits {
		e.ParamID = strings.TrimSpace(e.ParamID)
		e.DocID = strings.TrimSpace(e.DocID)
		e.Field = strings.TrimSpace(e.Field)
		e.Batch = strings.TrimSpace(e.Batch)

		if e.DocID != "" {
			// 文档编辑：Number / Name（batch 填批次定位单机文档，留空定位通用文档）
			if e.Field != "number" && e.Field != "name" {
				writeError(w, http.StatusBadRequest, fmt.Sprintf("编辑内容不合法: %s %s", e.DocID, e.Field))
				return
			}
			clean = append(clean, e)
			continue
		}

		if e.ParamID == "" || (e.Field != "value" && e.Field != "description") {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("编辑内容不合法: %s %s", e.ParamID, e.Field))
			return
		}
		if e.Field == "value" {
			if e.Batch == "" {
				writeError(w, http.StatusBadRequest, fmt.Sprintf("值编辑缺少批次号: %s", e.ParamID))
				return
			}
		}
		clean = append(clean, e)
	}

	path, err := currentDataFile(model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if path == "" {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 尚无数据包，请先上传", model))
		return
	}
	content, err := os.ReadFile(path)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "读取数据包失败")
		return
	}

	var out bytes.Buffer
	updated, missing, err := updateDataCells(content, clean, &out)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if updated == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]interface{}{
			"error":   "没有可写回的编辑（参数/批次未在数据包中定位到）",
			"missing": missing,
		})
		return
	}

	// 版本谱系：写回前旧版本自动快照，写回后的新内容同样入历史
	if _, err := snapshotCurrent(model, "auto", ""); err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "快照失败，请查看服务端日志")
		return
	}
	if err := os.WriteFile(path, out.Bytes(), 0o644); err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "写回数据包失败，请查看服务端日志")
		return
	}
	parseCachePop(model)
	note := strings.TrimSpace(body.Note)
	if note == "" {
		note = "表格编辑保存"
	}
	if _, err := snapshotCurrent(model, "edit", note); err != nil {
		log.Printf("%v", err)
	}
	log.Printf("型号 %s 数据包单元格编辑已写回: %d 处", model, updated)
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":   true,
		"updated":   updated,
		"missing":   missing,
		"data_file": fileInfo(path),
	})
}

func handleDatabaseParametersRoute(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Model string `json:"model"`
	}
	if err := decodeJSON(r, &body); err != nil || body.Model == "" {
		writeError(w, http.StatusBadRequest, "缺少 model（Go 版不支持内存缓存上传流程）")
		return
	}
	parsed, err := getParsedData(body.Model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if parsed == nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 尚无数据包，请先上传", body.Model))
		return
	}
	params := []map[string]interface{}{}
	for _, p := range parsed.Parameters {
		params = append(params, map[string]interface{}{
			"id":          p.ID,
			"description": p.Description,
			"values":      p.Data,
		})
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":       true,
		"batch_numbers": parsed.BatchNumbers,
		"parameters":    params,
	})
}

func handleConsistencyCompare(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Model        string   `json:"model"`
		BatchNumbers []string `json:"batch_numbers"`
		ParameterIDs []string `json:"parameter_ids"`
	}
	if err := decodeJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "请求数据为空")
		return
	}
	if len(body.BatchNumbers) == 0 {
		writeError(w, http.StatusBadRequest, "请至少选择一个批次")
		return
	}
	if len(body.ParameterIDs) == 0 {
		writeError(w, http.StatusBadRequest, "请至少选择一个参数")
		return
	}
	if body.Model == "" {
		writeError(w, http.StatusBadRequest, "缺少 model")
		return
	}
	parsed, err := getParsedData(body.Model)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if parsed == nil {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("型号 %q 尚无数据包，请先上传", body.Model))
		return
	}
	for _, b := range body.BatchNumbers {
		if !containsString(parsed.BatchNumbers, b) {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("批次号不存在: %s", b))
			return
		}
	}
	selectedIDs := map[string]bool{}
	for _, id := range body.ParameterIDs {
		selectedIDs[strings.TrimSpace(id)] = true
	}
	var params []*Param
	for _, p := range parsed.Parameters {
		if selectedIDs[p.ID] {
			params = append(params, p)
		}
	}

	var selectedBatches []string
	for _, b := range body.BatchNumbers {
		if b != "" {
			selectedBatches = append(selectedBatches, b)
		}
	}

	var buf bytes.Buffer
	if err := buildConsistencyDocx(&buf, selectedBatches, params); err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "生成一致性报告失败，请查看服务端日志")
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"success":         true,
		"output_filename": fmt.Sprintf("数据一致性对比_%s.docx", timeNow().Format("20060102_150405")),
		"file_type":       "docx",
		"report_content":  base64Encode(buf.Bytes()),
		"statistics": map[string]interface{}{
			"batch_count":        len(selectedBatches),
			"parameter_count":    len(params),
			"inconsistent_count": countInconsistent(selectedBatches, params),
		},
	})
}

// countInconsistent 重算不一致项数（与 buildConsistencyDocx 口径一致）
func countInconsistent(batches []string, params []*Param) int {
	count := 0
	for _, p := range params {
		var values []string
		for _, b := range batches {
			values = append(values, p.Data[b])
		}
		consistent, _ := consistencyRow(values)
		if !consistent {
			count++
		}
	}
	return count
}

func handleVersionDownload(w http.ResponseWriter, r *http.Request, model, vid string) {
	path, err := versionPath(model, vid)
	if err != nil {
		writeError(w, http.StatusNotFound, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
	w.Header().Set("Content-Disposition",
		mime.FormatMediaType("attachment", map[string]string{"filename": fmt.Sprintf("%s_数据包_%s.xlsx", model, vid)}))
	http.ServeFile(w, r, path)
}

func handleVersionRestore(w http.ResponseWriter, r *http.Request, model, vid string) {
	src, err := versionPath(model, vid)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if _, err := snapshotCurrent(model, "auto", ""); err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "快照失败，请查看服务端日志")
		return
	}
	root, err := modelRoot(model, true)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	entries, _ := os.ReadDir(root)
	for _, e := range entries {
		if !e.IsDir() && strings.EqualFold(filepath.Ext(e.Name()), ".xlsx") {
			if err := os.Remove(filepath.Join(root, e.Name())); err != nil {
				log.Printf("%v", err)
				writeError(w, http.StatusInternalServerError, "恢复版本失败，请查看服务端日志")
				return
			}
		}
	}
	if err := copyFile(src, filepath.Join(root, filepath.Base(src))); err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "恢复版本失败，请查看服务端日志")
		return
	}
	parseCachePop(model)
	log.Printf("型号 %s 已恢复版本 %s", model, vid)
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true})
}

func handleVersionDelete(w http.ResponseWriter, r *http.Request, model, vid string) {
	path, err := versionPath(model, vid)
	if err != nil {
		writeError(w, http.StatusNotFound, err.Error())
		return
	}
	if err := os.Remove(path); err != nil {
		log.Printf("%v", err)
		writeError(w, http.StatusInternalServerError, "删除版本失败，请查看服务端日志")
		return
	}
	meta, _ := readVersionsMeta(model)
	delete(meta, vid)
	if err := writeVersionsMeta(model, meta); err != nil {
		log.Printf("%v", err)
	}
	log.Printf("型号 %s 已删除版本 %s", model, vid)
	writeJSON(w, http.StatusOK, map[string]interface{}{"success": true})
}
