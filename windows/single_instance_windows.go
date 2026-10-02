package main

import (
	"errors"
	"fmt"
	"runtime"
	"time"
	"unsafe"

	"github.com/wailsapp/wails/v3/pkg/w32"
	"golang.org/x/sys/windows"
)

// Keep Wails beta.26's message receiver/callback, but fix its Windows sender:
// FindWindow cannot find its HWND_MESSAGE target, and notification must wait
// for a concurrently starting first instance. The outer mutex closes that race.
func desktopSingleInstance(key string) (release func(), secondary bool, err error) {
	name, err := windows.UTF16PtrFromString(`Local\Codexio-launch-` + key)
	if err != nil {
		return nil, false, err
	}
	handle, err := windows.CreateMutex(nil, false, name)
	if err != nil && !errors.Is(err, windows.ERROR_ALREADY_EXISTS) {
		return nil, false, fmt.Errorf("Codexio instance lock: %w", err)
	}
	release = func() { _ = windows.CloseHandle(handle) }
	if err == nil {
		return release, false, nil
	}
	defer release()
	err = desktopNotifyInstance(key)
	return nil, true, err
}

func desktopNotifyInstance(key string) error {
	user32 := windows.NewLazySystemDLL("user32.dll")
	find := user32.NewProc("FindWindowExW")
	allowForeground := user32.NewProc("AllowSetForegroundWindow")
	send := user32.NewProc("SendMessageTimeoutW")
	class, _ := windows.UTF16PtrFromString("wails-app-" + key + "-sic")
	name, _ := windows.UTF16PtrFromString("wails-app-" + key + "-siw")
	data, _ := windows.UTF16FromString(`{"additionalData":{"action":"show-main"}}`)
	packet := w32.COPYDATASTRUCT{
		DwData: w32.WMCOPYDATA_SINGLE_INSTANCE_DATA,
		CbData: uint32(len(data) * 2),
		LpData: uintptr(unsafe.Pointer(&data[0])),
	}
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		// HWND_MESSAGE is required for the receiver created by Wails.
		hwnd, _, _ := find.Call(uintptr(w32.HWND_MESSAGE), 0, uintptr(unsafe.Pointer(class)), uintptr(unsafe.Pointer(name)))
		if hwnd != 0 {
			_, pid := w32.GetWindowThreadProcessId(w32.HWND(hwnd))
			if pid != 0 {
				// Explorer grants the new launch foreground rights; pass them only
				// to the existing Codexio process before asking it to show its UI.
				_, _, _ = allowForeground.Call(uintptr(pid))
			}
			var result uintptr
			ok, _, _ := send.Call(hwnd, w32.WM_COPYDATA, 0, uintptr(unsafe.Pointer(&packet)), 0x0002, 1000, uintptr(unsafe.Pointer(&result)))
			if ok != 0 {
				runtime.KeepAlive(data)
				return nil
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	runtime.KeepAlive(data)
	return errors.New("已有同版本 Codexio 正在启动或暂时没有响应，请稍后再次打开")
}
