package main

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ======================================================================
// GenPaper Go 后端（无需 Python，单二进制）
// API 契约与 Flask 版完全一致，前端 frontend/dist 原样复用
// ======================================================================

func main() {
	log.SetFlags(log.Ldate | log.Ltime)
	log.Printf("GenPaper Go 后端 v%s", version)
	log.Printf("数据库根目录: %s", getDataRoot())
	log.Printf("前端目录: %s", getDistDir())

	mux := http.NewServeMux()
	mux.HandleFunc("/api/health", handleHealth)
	mux.HandleFunc("/api/database/overview", handleDatabaseOverview)
	mux.HandleFunc("/api/database/models", handleModelsRoute)
	mux.HandleFunc("/api/database/models/", handleModelsDispatch)
	mux.HandleFunc("/api/consistency/parameters", handleDatabaseParametersRoute)
	mux.HandleFunc("/api/consistency/compare", handleConsistencyCompare)
	mux.HandleFunc("/", handleStatic)

	port := os.Getenv("PORT")
	if port == "" {
		port = "5000"
	}
	pageURL := "http://127.0.0.1:" + port
	log.Printf("启动服务: %s", pageURL)
	fmt.Println("==================================================")
	fmt.Println("  GenPaper 单机数据包报告生成器 (Go 版)")
	fmt.Println("==================================================")
	fmt.Printf("  访问地址: %s\n", pageURL)
	fmt.Println("  托盘图标右键可复制地址或退出")
	fmt.Println("==================================================")

	// 先完成端口绑定，绑定失败时控制台仍然可见，便于看到错误
	listener, err := net.Listen("tcp", "127.0.0.1:"+port)
	if err != nil {
		log.Fatalf("服务启动失败: %v", err)
	}

	// 启动后最小化到系统托盘；右键托盘图标可「复制IP地址」或「退出」
	// GENPAPER_NO_TRAY=1 可禁用托盘（保留控制台，便于调试）
	noTray := strings.TrimSpace(os.Getenv("GENPAPER_NO_TRAY")) != ""
	if !noTray {
		startTray(pageURL)
	} else {
		log.Printf("GENPAPER_NO_TRAY 已设置，保持控制台窗口运行")
	}

	// 控制台已隐藏，由程序自己把页面交给默认浏览器（GENPAPER_NO_BROWSER=1 可禁用）
	if strings.TrimSpace(os.Getenv("GENPAPER_NO_BROWSER")) == "" {
		go func() {
			// 等服务真正可以响应再打开，避免浏览器先到而显示连接失败
			time.Sleep(600 * time.Millisecond)
			launchBrowser(pageURL)
		}()
	}

	if err := http.Serve(listener, mux); err != nil {
		log.Fatalf("服务运行失败: %v", err)
	}
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"status":    "ok",
		"timestamp": timeNow().Format("2006-01-02T15:04:05.000000"),
		// 兼容 Flask 版响应字段；Go 版无内存上传缓存
		"memory_cache_size": 0,
	})
}

// handleModelsRoute POST /api/database/models（新建型号）
func handleModelsRoute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "方法不允许")
		return
	}
	handleDatabaseCreateModel(w, r)
}

// handleModelsDispatch /api/database/models/<model>[/<action>] 路由分发
func handleModelsDispatch(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/api/database/models/")
	segments := strings.Split(rest, "/")
	model := segments[0]
	if model == "" {
		writeError(w, http.StatusNotFound, map[string]interface{}{"error": "API 端点不存在"})
		return
	}

	var action string
	if len(segments) > 1 {
		action = segments[1]
	}
	var sub string
	if len(segments) > 2 {
		sub = segments[2]
	}

	switch {
	case len(segments) == 1 && r.Method == http.MethodGet:
		handleDatabaseDetail(w, r, model)
	case action == "data" && sub == "" && r.Method == http.MethodPost:
		handleDatabaseUploadData(w, r, model)
	case action == "data" && sub == "update" && r.Method == http.MethodPost:
		handleDatabaseUpdateData(w, r, model)
	case action == "templates" && sub == "" && r.Method == http.MethodPost:
		handleDatabaseUploadTemplate(w, r, model)
	case action == "templates" && sub == "config" && r.Method == http.MethodPost:
		handleDatabaseTemplateConfig(w, r, model)
	case action == "templates" && sub != "" && sub != "config" && len(segments) == 4 && segments[3] == "check" && r.Method == http.MethodGet:
		handleTemplateCheck(w, r, model, sub)
	case action == "generate" && r.Method == http.MethodPost:
		handleDatabaseGenerate(w, r, model)
	case action == "generate_all" && r.Method == http.MethodPost:
		handleDatabaseGenerateAll(w, r, model)
	case action == "parameters" && r.Method == http.MethodGet:
		handleDatabaseParameters(w, r, model)
	case action == "open_folder" && sub == "" && r.Method == http.MethodPost:
		handleDatabaseOpenFolder(w, r, model)
	case action == "versions" && sub != "" && r.Method == http.MethodGet:
		handleVersionDownload(w, r, model, sub)
	case action == "versions" && sub != "" && r.Method == http.MethodPost:
		handleVersionRestore(w, r, model, sub)
	case action == "versions" && sub != "" && r.Method == http.MethodDelete:
		handleVersionDelete(w, r, model, sub)
	default:
		writeError(w, http.StatusNotFound, map[string]interface{}{"error": "API 端点不存在"})
	}
}

// handleStatic 静态文件 + SPA 回退（对应 Python 版 index/404 处理）
func handleStatic(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" && strings.HasPrefix(r.URL.Path, "/api/") {
		writeError(w, http.StatusNotFound, "API 端点不存在")
		return
	}
	dist := getDistDir()
	clean := filepath.Clean("/" + r.URL.Path)
	full := filepath.Join(dist, clean)

	// 程序更新后浏览器不得继续使用旧缓存：强制协商缓存（未变更时 304）
	w.Header().Set("Cache-Control", "no-cache")

	base := filepath.Base(r.URL.Path)
	if base != "/" && strings.Contains(base, ".") {
		if fileExists(full) {
			http.ServeFile(w, r, full)
			return
		}
		writeError(w, http.StatusNotFound, "Not Found")
		return
	}
	if r.URL.Path != "/" {
		if st, err := os.Stat(full); err == nil && !st.IsDir() {
			http.ServeFile(w, r, full)
			return
		}
	}
	// SPA 回退到 index.html
	index := filepath.Join(dist, "index.html")
	if !fileExists(index) {
		http.Error(w, "frontend/dist/index.html not found", http.StatusNotFound)
		return
	}
	http.ServeFile(w, r, index)
}

// ======================================================================
// 小工具
// ======================================================================

func fileExists(path string) bool {
	st, err := os.Stat(path)
	return err == nil && !st.IsDir()
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	return err
}

func containsString(list []string, target string) bool {
	for _, s := range list {
		if s == target {
			return true
		}
	}
	return false
}

func base64Encode(b []byte) string {
	return base64.StdEncoding.EncodeToString(b)
}

func decodeJSON(r *http.Request, v interface{}) error {
	defer r.Body.Close()
	dec := json.NewDecoder(r.Body)
	return dec.Decode(v)
}
