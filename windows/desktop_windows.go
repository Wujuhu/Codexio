package main

import (
	"codexio/windows/backend"
	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/w32"
	"io/fs"
	"os"
	"path/filepath"
	"unsafe"
)

type desktopIcon struct{ small, large w32.HICON }

func (h *desktopHost) applyWindowAppearance(w *application.WebviewWindow) {
	c := h.service.Config()
	id := backend.ValueString(c["app_icon"])
	if id != "main" {
		if _, e := fs.Stat(h.assets, "brand/app-icons/"+id+".png"); e != nil {
			id = "main"
		}
	}
	h.iconMu.Lock()
	icon, exists := h.icons[id]
	if !exists {
		bytes := h.brandIcon(id)
		icon.small, _ = w32.CreateSmallHIconFromImage(bytes)
		icon.large, _ = w32.CreateLargeHIconFromImage(bytes)
		h.icons[id] = icon
	}
	h.iconMu.Unlock()
	application.InvokeSync(func() {
		hwnd := w32.HWND(uintptr(w.NativeWindow()))
		if hwnd == 0 {
			return
		}
		if icon.small != 0 {
			w32.SendMessage(hwnd, w32.WM_SETICON, w32.ICON_SMALL, uintptr(icon.small))
		}
		if icon.large != 0 {
			w32.SendMessage(hwnd, w32.WM_SETICON, w32.ICON_BIG, uintptr(icon.large))
		}
		dark := w32.IsSystemCurrentlyDarkMode()
		if backend.ValueString(c["theme"]) == "dark" {
			dark = true
		} else if backend.ValueString(c["theme"]) == "light" {
			dark = false
		}
		w32.SetTheme(uintptr(hwnd), dark)
	})
}
func (h *desktopHost) releaseIcons() {
	h.iconMu.Lock()
	defer h.iconMu.Unlock()
	for _, icon := range h.icons {
		if icon.small != 0 {
			w32.DestroyIcon(icon.small)
		}
		if icon.large != 0 && icon.large != icon.small {
			w32.DestroyIcon(icon.large)
		}
	}
	h.icons = map[string]desktopIcon{}
}
func desktopPlaceBottom(w *application.WebviewWindow) {
	application.InvokeSync(func() {
		hwnd := w32.HWND(uintptr(w.NativeWindow()))
		if hwnd != 0 {
			w32.SetWindowPos(hwnd, w32.HWND_BOTTOM, 0, 0, 0, 0, w32.SWP_NOMOVE|w32.SWP_NOSIZE|w32.SWP_NOACTIVATE)
		}
	})
}
func desktopOpenPath(path string) error {
	absolute, e := filepath.Abs(path)
	if e != nil {
		return e
	}
	if _, e = os.Stat(absolute); e != nil {
		return e
	}
	return application.InvokeSyncWithResult(func() error {
		return w32.ShellExecute(0, "open", absolute, "", filepath.Dir(absolute), w32.SW_SHOWNORMAL)
	})
}
func desktopError(message string) { w32.MessageBox(0, message, "Codexio", w32.MB_OK|w32.MB_ICONERROR) }
func desktopWindowVisible(w *application.WebviewWindow) bool {
	return w != nil && w.NativeWindow() != unsafe.Pointer(nil) && w.IsVisible()
}
