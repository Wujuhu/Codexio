package main

import (
	"codexio/windows/backend"
	"encoding/binary"
	"fmt"
	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/w32"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"unsafe"
)

type desktopIcon struct {
	small, large         w32.HICON
	smallSize, largeSize int
}

func (h *desktopHost) applyWindowAppearance(w *application.WebviewWindow) {
	c := h.service.Config()
	id := backend.ValueString(c["app_icon"])
	if id != "main" {
		if _, e := fs.Stat(h.assets, "brand/app-icons/"+id+".png"); e != nil {
			id = "main"
		}
	}
	application.InvokeSync(func() {
		hwnd := w32.HWND(uintptr(w.NativeWindow()))
		if hwnd == 0 {
			return
		}
		dpi := w32.GetDpiForWindow(hwnd)
		if dpi == 0 {
			dpi = 96
		}
		small := max(16, w32.GetSystemMetricsForDpi(w32.SM_CXSMICON, dpi))
		large := max(32, w32.GetSystemMetricsForDpi(w32.SM_CXICON, dpi))
		// One entry per window kind and configured icon: at most 2 × 13.
		// Replace DPI variants only after the window has received the new handles.
		kind := "main"
		if w.Name() == "Codexio-floating" {
			kind = "floating"
		}
		key := kind + ":" + id
		h.iconMu.Lock()
		defer h.iconMu.Unlock()
		icon := h.icons[key]
		old := icon
		if icon.smallSize != small || icon.largeSize != large {
			data := h.brandIcon(id)
			icon.small, _ = desktopSizedIcon(data, small)
			icon.large, _ = desktopSizedIcon(data, large)
			icon.smallSize, icon.largeSize = small, large
			h.icons[key] = icon
		}
		if icon.small != 0 {
			w32.SendMessage(hwnd, w32.WM_SETICON, w32.ICON_SMALL, uintptr(icon.small))
		}
		if icon.large != 0 {
			w32.SendMessage(hwnd, w32.WM_SETICON, w32.ICON_BIG, uintptr(icon.large))
		}
		if old.small != 0 && old.small != icon.small {
			w32.DestroyIcon(old.small)
		}
		if old.large != 0 && old.large != old.small && old.large != icon.large {
			w32.DestroyIcon(old.large)
		}
		dark := w32.IsSystemCurrentlyDarkMode()
		if backend.ValueString(c["theme"]) == "dark" {
			dark = true
		} else if backend.ValueString(c["theme"]) == "light" {
			dark = false
		}
		if w.Name() == "Codexio-floating" {
			dark = true
		}
		w32.SetTheme(uintptr(hwnd), dark)
	})
	if w.Name() == "Codexio-floating" {
		w.SetBackgroundColour(application.NewRGBA(0, 0, 0, 0))
	}
}

// Adapt Wails w32/icon.go's embedded PNG-in-ICO loading to the target window's
// physical size. Its standard helpers use system-wide metrics, not this DPI.
func desktopSizedIcon(data []byte, size int) (w32.HICON, error) {
	resource := data
	if len(data) >= 6 && string(data[:4]) == "\x00\x00\x01\x00" {
		count := int(binary.LittleEndian.Uint16(data[4:6]))
		if count == 0 || count > (len(data)-6)/16 {
			return 0, fmt.Errorf("invalid window ICO directory")
		}
		best := 0
		for i := 0; i < count; i++ {
			entry := data[6+i*16 : 6+(i+1)*16]
			width := int(entry[0])
			if width == 0 {
				width = 256
			}
			if best != 0 && (best >= size && (width < size || width >= best) || best < size && width <= best) {
				continue
			}
			length := uint64(binary.LittleEndian.Uint32(entry[8:12]))
			offset := uint64(binary.LittleEndian.Uint32(entry[12:16]))
			if length == 0 || offset < uint64(6+count*16) || offset+length > uint64(len(data)) {
				return 0, fmt.Errorf("invalid window ICO image")
			}
			resource = data[offset : offset+length]
			best = width
		}
	}
	if len(resource) < 8 {
		return 0, fmt.Errorf("empty window icon")
	}
	icon, err := w32.CreateIconFromResourceEx(uintptr(unsafe.Pointer(&resource[0])), uint32(len(resource)), true, 0x00030000, size, size, 0)
	runtime.KeepAlive(resource)
	return w32.HICON(icon), err
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
