//go:build !windows

package main

// 非 Windows 平台没有系统托盘：空实现，程序按普通控制台方式运行。

import "log"

func startTray(address string) {
	log.Printf("当前平台不支持系统托盘，保持控制台窗口（访问地址: %s）", address)
}
