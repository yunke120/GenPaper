//go:build windows

package main

// ======================================================================
// 系统托盘（纯 syscall 实现，无 cgo、无第三方依赖）
//
// 行为：程序启动后创建托盘图标，并把控制台窗口隐藏（= 最小化到托盘）。
// 右键托盘图标弹出菜单：复制IP地址 / 退出。
// 设 GENPAPER_NO_TRAY=1 可禁用托盘并保留控制台窗口（调试用）。
// ======================================================================

import (
	"log"
	"os"
	"runtime"
	"syscall"
	"time"
	"unsafe"
)

// ---------------- Win32 常量 ----------------

const (
	wmNull        = 0x0000
	wmDestroy     = 0x0002
	wmContextMenu = 0x007B
	wmRButtonUp   = 0x0205
	wmApp         = 0x8000

	trayCallbackMessage = wmApp + 1

	nimAdd    = 0
	nimModify = 1
	nimDelete = 2

	nifMessage = 0x00000001
	nifIcon    = 0x00000002
	nifTip     = 0x00000004
	nifInfo    = 0x00000010

	niifInfo = 0x00000001

	mfString    = 0x00000000
	mfSeparator = 0x00000800

	tpmLeftAlign   = 0x0000
	tpmRightButton = 0x0002
	tpmNoNotify    = 0x0080
	tpmReturnCmd   = 0x0100

	imageIcon    = 1
	lrDefaultSize = 0x0040
	lrShared      = 0x8000

	swHide = 0

	gmemMoveable  = 0x0002
	cfUnicodeText = 13

	wsPopup = 0x80000000

	errorClassAlreadyExists = 1410

	// 托盘菜单命令 ID
	trayCmdCopyAddress = 1
	trayCmdQuit        = 2
)

// ---------------- Win32 结构体 ----------------

type point struct {
	X int32
	Y int32
}

type msgStruct struct {
	HWnd     syscall.Handle
	Message  uint32
	WParam   uintptr
	LParam   uintptr
	Time     uint32
	Pt       point
	LPrivate uint32
}

type wndClassEx struct {
	CbSize        uint32
	Style         uint32
	LpfnWndProc   uintptr
	CbClsExtra    int32
	CbWndExtra    int32
	HInstance     syscall.Handle
	HIcon         syscall.Handle
	HCursor       syscall.Handle
	HbrBackground syscall.Handle
	LpszMenuName  *uint16
	LpszClassName *uint16
	HIconSm       syscall.Handle
}

type winGUID struct {
	Data1 uint32
	Data2 uint16
	Data3 uint16
	Data4 [8]byte
}

// notifyIconData 对应 NOTIFYICONDATAW
type notifyIconData struct {
	CbSize           uint32
	HWnd             syscall.Handle
	UID              uint32
	UFlags           uint32
	UCallbackMessage uint32
	HIcon            syscall.Handle
	SzTip            [128]uint16
	DwState          uint32
	DwStateMask      uint32
	SzInfo           [256]uint16
	UVersion         uint32 // 与 uTimeout 共用
	SzInfoTitle      [64]uint16
	DwInfoFlags      uint32
	GuidItem         winGUID
	HBalloonIcon     syscall.Handle
}

// ---------------- DLL / API ----------------

var (
	user32   = syscall.NewLazyDLL("user32.dll")
	shell32  = syscall.NewLazyDLL("shell32.dll")
	kernel32 = syscall.NewLazyDLL("kernel32.dll")

	procRegisterClassExW    = user32.NewProc("RegisterClassExW")
	procCreateWindowExW     = user32.NewProc("CreateWindowExW")
	procDefWindowProcW      = user32.NewProc("DefWindowProcW")
	procGetMessageW         = user32.NewProc("GetMessageW")
	procTranslateMessage    = user32.NewProc("TranslateMessage")
	procDispatchMessageW    = user32.NewProc("DispatchMessageW")
	procPostQuitMessage     = user32.NewProc("PostQuitMessage")
	procPostMessageW        = user32.NewProc("PostMessageW")
	procGetCursorPos        = user32.NewProc("GetCursorPos")
	procSetForegroundWindow = user32.NewProc("SetForegroundWindow")
	procCreatePopupMenu     = user32.NewProc("CreatePopupMenu")
	procAppendMenuW         = user32.NewProc("AppendMenuW")
	procTrackPopupMenu      = user32.NewProc("TrackPopupMenu")
	procDestroyMenu         = user32.NewProc("DestroyMenu")
	procLoadImageW          = user32.NewProc("LoadImageW")
	procShowWindow          = user32.NewProc("ShowWindow")
	procOpenClipboard       = user32.NewProc("OpenClipboard")
	procEmptyClipboard      = user32.NewProc("EmptyClipboard")
	procSetClipboardData    = user32.NewProc("SetClipboardData")
	procCloseClipboard      = user32.NewProc("CloseClipboard")

	procGetConsoleWindow = kernel32.NewProc("GetConsoleWindow")
	procGetModuleHandleW = kernel32.NewProc("GetModuleHandleW")
	procGlobalAlloc      = kernel32.NewProc("GlobalAlloc")
	procGlobalLock       = kernel32.NewProc("GlobalLock")
	procGlobalUnlock     = kernel32.NewProc("GlobalUnlock")
	procGlobalFree       = kernel32.NewProc("GlobalFree")
	procRtlMoveMemory    = kernel32.NewProc("RtlMoveMemory")

	procShellNotifyIconW = shell32.NewProc("Shell_NotifyIconW")
)

// 托盘状态（窗口过程与消息循环在同一线程上访问）
var (
	trayClassName = "GenPaperTrayWindow"
	trayWndProc   = syscall.NewCallback(trayWindowProc)
	trayWindow    syscall.Handle
	trayIconData  notifyIconData
	trayAddress   = "http://127.0.0.1:5000"
)

// startTray 创建托盘图标并隐藏控制台窗口；address 为「复制IP地址」复制的内容。
func startTray(address string) {
	if address != "" {
		trayAddress = address
	}
	ready := make(chan bool, 1)
	go func() {
		runtime.LockOSThread() // 窗口与消息循环必须同线程
		defer runtime.UnlockOSThread()
		if !initTray() {
			ready <- false
			return
		}
		ready <- true
		messageLoop()
	}()

	select {
	case ok := <-ready:
		if !ok {
			log.Printf("托盘创建失败，保留控制台窗口")
			return
		}
		hideConsoleWindow()
		log.Printf("已最小化到托盘，右键托盘图标可「复制IP地址」或「退出」")
	case <-time.After(5 * time.Second):
		log.Printf("托盘初始化超时，保留控制台窗口")
	}
}

// initTray 注册窗口类、创建隐藏窗口并添加托盘图标
func initTray() bool {
	hInstance, _, _ := procGetModuleHandleW.Call(0)
	className, _ := syscall.UTF16PtrFromString(trayClassName)

	wc := wndClassEx{
		CbSize:        uint32(unsafe.Sizeof(wndClassEx{})),
		LpfnWndProc:   trayWndProc,
		HInstance:     syscall.Handle(hInstance),
		LpszClassName: className,
	}
	if r, _, err := procRegisterClassExW.Call(uintptr(unsafe.Pointer(&wc))); r == 0 {
		// 同一进程内重复注册不算失败
		if err != syscall.Errno(errorClassAlreadyExists) {
			log.Printf("RegisterClassEx 失败: %v", err)
			return false
		}
	}

	title, _ := syscall.UTF16PtrFromString("GenPaper")
	hwnd, _, err := procCreateWindowExW.Call(
		0,
		uintptr(unsafe.Pointer(className)),
		uintptr(unsafe.Pointer(title)),
		uintptr(wsPopup),
		0, 0, 0, 0,
		0, 0,
		hInstance,
		0,
	)
	if hwnd == 0 {
		log.Printf("CreateWindowEx 失败: %v", err)
		return false
	}
	trayWindow = syscall.Handle(hwnd)

	icon := loadTrayIcon(hInstance)
	nid := notifyIconData{
		CbSize:           uint32(unsafe.Sizeof(notifyIconData{})),
		HWnd:             trayWindow,
		UID:              1,
		UFlags:           nifMessage | nifIcon | nifTip,
		UCallbackMessage: trayCallbackMessage,
		HIcon:            syscall.Handle(icon),
	}
	copyUTF16(nid.SzTip[:], "GenPaper 单机数据包报告生成器")

	if r, _, err := procShellNotifyIconW.Call(nimAdd, uintptr(unsafe.Pointer(&nid))); r == 0 {
		log.Printf("Shell_NotifyIcon 失败: %v", err)
		return false
	}
	trayIconData = nid
	return true
}

// loadTrayIcon 加载可执行文件中的图标资源（RT_GROUP_ICON #1），失败时回落到系统图标
func loadTrayIcon(hInstance uintptr) uintptr {
	if r, _, _ := procLoadImageW.Call(
		hInstance,
		uintptr(1), // MAKEINTRESOURCE(1)
		imageIcon,
		0, 0,
		lrDefaultSize|lrShared,
	); r != 0 {
		return r
	}
	// IDI_APPLICATION = 32512
	if r, _, _ := procLoadImageW.Call(0, uintptr(32512), imageIcon, 0, 0, lrDefaultSize|lrShared); r != 0 {
		return r
	}
	return 0
}

func messageLoop() {
	var m msgStruct
	for {
		r, _, _ := procGetMessageW.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if int32(r) <= 0 { // 0 = WM_QUIT, -1 = 错误
			return
		}
		procTranslateMessage.Call(uintptr(unsafe.Pointer(&m)))
		procDispatchMessageW.Call(uintptr(unsafe.Pointer(&m)))
	}
}

func trayWindowProc(hwnd syscall.Handle, msg uint32, wparam, lparam uintptr) uintptr {
	switch msg {
	case trayCallbackMessage:
		switch uint32(lparam) & 0xFFFF {
		case wmRButtonUp, wmContextMenu:
			showTrayMenu(hwnd)
		}
		return 0
	case wmDestroy:
		procPostQuitMessage.Call(0)
		return 0
	}
	r, _, _ := procDefWindowProcW.Call(uintptr(hwnd), uintptr(msg), wparam, lparam)
	return r
}

// showTrayMenu 在鼠标位置弹出右键菜单
func showTrayMenu(hwnd syscall.Handle) {
	hmenu, _, _ := procCreatePopupMenu.Call()
	if hmenu == 0 {
		return
	}
	defer procDestroyMenu.Call(hmenu)

	appendMenu(hmenu, mfString, trayCmdCopyAddress, "复制IP地址")
	appendMenu(hmenu, mfSeparator, 0, "")
	appendMenu(hmenu, mfString, trayCmdQuit, "退出")

	var pt point
	procGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
	procSetForegroundWindow.Call(uintptr(hwnd))
	cmd, _, _ := procTrackPopupMenu.Call(
		hmenu,
		uintptr(tpmLeftAlign|tpmRightButton|tpmNoNotify|tpmReturnCmd),
		uintptr(pt.X),
		uintptr(pt.Y),
		0,
		uintptr(hwnd),
		0,
	)
	// 让菜单在点击别处时正常关闭（MSDN 建议）
	procPostMessageW.Call(uintptr(hwnd), wmNull, 0, 0)

	switch cmd {
	case trayCmdCopyAddress:
		trayCopyAddress()
	case trayCmdQuit:
		trayQuit()
	}
}

func appendMenu(hmenu uintptr, flags, id uintptr, text string) {
	var p *uint16
	if text != "" {
		p, _ = syscall.UTF16PtrFromString(text)
	}
	procAppendMenuW.Call(hmenu, flags, id, uintptr(unsafe.Pointer(p)))
}

// trayCopyAddress 把访问地址写入剪贴板，并弹气泡提示
func trayCopyAddress() {
	if err := copyToClipboard(trayAddress); err != nil {
		log.Printf("复制访问地址失败: %v", err)
		trayBalloon("复制失败", err.Error())
		return
	}
	log.Printf("已复制访问地址: %s", trayAddress)
	trayBalloon("已复制访问地址", trayAddress)
}

// trayBalloon 通过托盘图标弹气泡提示（控制台已隐藏，用它给反馈）
func trayBalloon(title, text string) {
	nid := trayIconData
	nid.UFlags = nifInfo
	copyUTF16(nid.SzInfoTitle[:], title)
	copyUTF16(nid.SzInfo[:], text)
	nid.DwInfoFlags = niifInfo
	procShellNotifyIconW.Call(nimModify, uintptr(unsafe.Pointer(&nid)))
}

func trayQuit() {
	procShellNotifyIconW.Call(nimDelete, uintptr(unsafe.Pointer(&trayIconData)))
	log.Printf("从托盘退出程序")
	os.Exit(0)
}

func copyToClipboard(s string) error {
	if r, _, err := procOpenClipboard.Call(0); r == 0 {
		return err
	}
	defer procCloseClipboard.Call()
	procEmptyClipboard.Call()

	u, err := syscall.UTF16FromString(s)
	if err != nil {
		return err
	}
	size := uintptr(len(u) * 2)
	h, _, gerr := procGlobalAlloc.Call(gmemMoveable, size)
	if h == 0 {
		return gerr
	}
	ptr, _, _ := procGlobalLock.Call(h)
	if ptr == 0 {
		procGlobalFree.Call(h)
		return syscall.EINVAL
	}
	// 用 RtlMoveMemory 复制，避免 uintptr→unsafe.Pointer 的转换（go vet unsafeptr）
	procRtlMoveMemory.Call(ptr, uintptr(unsafe.Pointer(&u[0])), size)
	procGlobalUnlock.Call(h)

	if r, _, serr := procSetClipboardData.Call(cfUnicodeText, h); r == 0 {
		procGlobalFree.Call(h)
		return serr
	}
	return nil
}

func hideConsoleWindow() {
	hwnd, _, _ := procGetConsoleWindow.Call()
	if hwnd != 0 {
		procShowWindow.Call(hwnd, swHide)
	}
}

// copyUTF16 把字符串写入固定长度 UTF-16 缓冲区（含结尾 0）
func copyUTF16(dst []uint16, s string) {
	if len(dst) == 0 {
		return
	}
	u, err := syscall.UTF16FromString(s)
	if err != nil {
		dst[0] = 0
		return
	}
	if len(u) > len(dst) {
		u = u[:len(dst)]
		u[len(u)-1] = 0
	}
	copy(dst, u)
}
