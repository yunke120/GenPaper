package main

import (
	"log"
	"os/exec"
	"runtime"
)

// ======================================================================
// 用系统默认浏览器打开地址
//
// 程序启动后会自动最小化到托盘，控制台不可见，
// 因此由程序自己把页面交给默认浏览器，用户无需知道端口。
// URL 由程序内部拼装（固定 http://127.0.0.1:<port>），不经过 shell。
// ======================================================================

func openInDefaultBrowser(url string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		// url.dll 的 FileProtocolHandler 就是资源管理器打开链接的方式，
		// 参数直接传递，不经过 cmd.exe
		cmd = exec.Command("rundll32.exe", "url.dll,FileProtocolHandler", url)
	case "darwin":
		cmd = exec.Command("open", url)
	default:
		cmd = exec.Command("xdg-open", url)
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	go func() { _ = cmd.Wait() }()
	return nil
}

// launchBrowser 打开访问页面；失败只记日志，不影响服务
func launchBrowser(url string) {
	if err := openInDefaultBrowser(url); err != nil {
		log.Printf("自动打开浏览器失败（请手动访问 %s）: %v", url, err)
		return
	}
	log.Printf("已在默认浏览器中打开: %s", url)
}
