package main

import (
	"fmt"
	"os"
	"os/exec"
	"runtime"
)

// ======================================================================
// 打开型号文件夹（在运行后端的这台机器上用系统文件管理器打开目录）
//
// GenPaper 是绑定 127.0.0.1 的单机工具，前端与后端必然在同一台桌面机器上，
// 因此由后端调用系统文件管理器即可满足「打开数据包文件夹」的需求。
// ======================================================================

// openFolderInFileManager 使用系统文件管理器打开 dir。
// 命令通过 exec.Command 直接传参（不经过 shell），避免路径中的特殊字符被解释。
func openFolderInFileManager(dir string) error {
	st, err := os.Stat(dir)
	if err != nil {
		return fmt.Errorf("目录不存在: %s", dir)
	}
	if !st.IsDir() {
		return fmt.Errorf("不是目录: %s", dir)
	}

	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		// explorer.exe 的退出码不可靠（成功打开也可能返回 1），因此只校验启动错误
		cmd = exec.Command("explorer.exe", dir)
	case "darwin":
		cmd = exec.Command("open", dir)
	default:
		cmd = exec.Command("xdg-open", dir)
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	// 异步回收子进程，避免僵尸进程；退出码不做判断
	go func() { _ = cmd.Wait() }()
	return nil
}
