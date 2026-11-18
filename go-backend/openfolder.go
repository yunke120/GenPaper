package main

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
)

// ======================================================================
// 在运行后端的这台机器上用系统文件管理器打开目录
//
// GenPaper 只监听 127.0.0.1，前端与后端必然在同一台桌面机器上，
// 因此由后端调用系统文件管理器即可满足「打开数据包文件夹」的需求。
// ======================================================================

// openFolderInFileManager 打开 dir，并把窗口带到最前。
func openFolderInFileManager(dir string) error {
	st, err := os.Stat(dir)
	if err != nil {
		return fmt.Errorf("目录不存在: %s", dir)
	}
	if !st.IsDir() {
		return fmt.Errorf("不是目录: %s", dir)
	}

	switch runtime.GOOS {
	case "windows":
		return openFolderWindows(dir)
	case "darwin":
		return startDetached("open", dir)
	default:
		return startDetached("xdg-open", dir)
	}
}

// startDetached 启动命令并异步回收，避免僵尸进程；不检查退出码。
func startDetached(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	if err := cmd.Start(); err != nil {
		return err
	}
	go func() { _ = cmd.Wait() }()
	return nil
}
