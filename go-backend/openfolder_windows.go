//go:build windows

package main

// ======================================================================
// Windows：用 ShellExecuteW 打开目录（shell 的「打开」动作，等同在资源
// 管理器里打开该文件夹），随后把新出现的资源管理器窗口提到前台。
//
// 为什么不用 exec.Command("explorer.exe", dir)：
// explorer.exe 是单实例外壳进程，从服务/后台进程启动时新窗口常被留在
// 其他窗口之后（Windows 前台锁），用户看起来就像「没反应」。
// ShellExecuteW 由 shell 自己创建窗口，行为与双击文件夹一致；
// 这里再显式 SetForegroundWindow 兜一次，确保窗口真的弹到最前。
//
// 本文件的标识符统一用 of 前缀，避免与 tray_windows.go 的 Win32 声明冲突。
// ======================================================================

import (
	"log"
	"syscall"
	"time"
	"unsafe"
)

var (
	ofUser32  = syscall.NewLazyDLL("user32.dll")
	ofShell32 = syscall.NewLazyDLL("shell32.dll")
	ofOle32   = syscall.NewLazyDLL("ole32.dll")

	procOfShellExecuteW     = ofShell32.NewProc("ShellExecuteW")
	procOfCoInitializeEx    = ofOle32.NewProc("CoInitializeEx")
	procOfEnumWindows       = ofUser32.NewProc("EnumWindows")
	procOfGetClassNameW     = ofUser32.NewProc("GetClassNameW")
	procOfIsWindowVisible   = ofUser32.NewProc("IsWindowVisible")
	procOfShowWindow        = ofUser32.NewProc("ShowWindow")
	procOfSetForegroundWin  = ofUser32.NewProc("SetForegroundWindow")
	procOfBringWindowToTop  = ofUser32.NewProc("BringWindowToTop")
	procOfSetWindowPos      = ofUser32.NewProc("SetWindowPos")
	procOfGetForegroundWin  = ofUser32.NewProc("GetForegroundWindow")
	procOfKeybdEvent        = ofUser32.NewProc("keybd_event")
)

const (
	ofSWShowNormal = 1
	ofSWRestore    = 9
	ofVKMenu       = 0x12 // Alt：解除前台锁用
	ofKeyUp        = 0x0002

	ofHWNDTopmost   = ^uintptr(0) // -1
	ofHWNDNotTopmost = ^uintptr(1) // -2

	ofSWPNoSize     = 0x0001
	ofSWPNoMove     = 0x0002
	ofSWPNoActivate = 0x0010

	ofCoInitApartmentThreaded = 0x2
)

// openFolderWindows 用 shell 打开目录并把窗口前置
func openFolderWindows(dir string) error {
	verb, _ := syscall.UTF16PtrFromString("open")
	path, err := syscall.UTF16PtrFromString(dir)
	if err != nil {
		return err
	}

	// ShellExecuteW 依赖 COM；某些 shell 扩展在未初始化时会失败
	procOfCoInitializeEx.Call(0, ofCoInitApartmentThreaded)

	// 记录打开前可见的资源管理器窗口，用于定位新窗口
	before := ofVisibleExplorerWindows()

	ret, _, callErr := procOfShellExecuteW.Call(
		0,
		uintptr(unsafe.Pointer(verb)),
		uintptr(unsafe.Pointer(path)),
		0,
		0,
		ofSWShowNormal,
	)
	if ret <= 32 {
		// ShellExecute 返回值 <= 32 即失败，回退到 explorer.exe
		log.Printf("ShellExecuteW 打开目录失败(ret=%d, %v)，回退 explorer.exe", ret, callErr)
		return startDetached("explorer.exe", dir)
	}

	go ofFocusNewExplorerWindow(before, dir)
	return nil
}

// ofFocusNewExplorerWindow 等待新的资源管理器窗口出现并前置
func ofFocusNewExplorerWindow(before map[uintptr]bool, dir string) {
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		time.Sleep(120 * time.Millisecond)
		for hwnd := range ofVisibleExplorerWindows() {
			if before[hwnd] {
				continue
			}
			if ofBringToFront(hwnd) {
				log.Printf("已打开并前置文件夹窗口: %s", dir)
				return
			}
		}
	}
	log.Printf("已打开文件夹（未能确认前置）: %s", dir)
}

// ofBringToFront 解除前台锁并把窗口提到最前
func ofBringToFront(hwnd uintptr) bool {
	if fg, _, _ := procOfGetForegroundWin.Call(); fg != 0 && fg != hwnd {
		// 前台锁：向当前前台窗口按一次 Alt，之后 SetForegroundWindow 才会被允许
		procOfKeybdEvent.Call(ofVKMenu, 0, 0, 0)
		procOfKeybdEvent.Call(ofVKMenu, 0, ofKeyUp, 0)
	}
	procOfShowWindow.Call(hwnd, ofSWRestore)
	procOfBringWindowToTop.Call(hwnd)
	// 先置顶再取消置顶，确保压过其他 topmost 窗口
	procOfSetWindowPos.Call(hwnd, ofHWNDTopmost, 0, 0, 0, 0, ofSWPNoMove|ofSWPNoSize|ofSWPNoActivate)
	procOfSetWindowPos.Call(hwnd, ofHWNDNotTopmost, 0, 0, 0, 0, ofSWPNoMove|ofSWPNoSize|ofSWPNoActivate)
	procOfShowWindow.Call(hwnd, ofSWShowNormal)
	r, _, _ := procOfSetForegroundWin.Call(hwnd)
	return r != 0
}

// ofVisibleExplorerWindows 返回当前可见的资源管理器窗口集合
func ofVisibleExplorerWindows() map[uintptr]bool {
	out := map[uintptr]bool{}
	cb := syscall.NewCallback(func(hwnd uintptr, _ uintptr) uintptr {
		if r, _, _ := procOfIsWindowVisible.Call(hwnd); r == 0 {
			return 1
		}
		var buf [256]uint16
		n, _, _ := procOfGetClassNameW.Call(hwnd, uintptr(unsafe.Pointer(&buf[0])), uintptr(len(buf)))
		if n == 0 {
			return 1
		}
		name := syscall.UTF16ToString(buf[:n])
		if name == "CabinetWClass" || name == "ExploreWClass" {
			out[hwnd] = true
		}
		return 1
	})
	procOfEnumWindows.Call(cb, 0)
	return out
}
