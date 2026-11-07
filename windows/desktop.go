package main

import (
	"codexio/windows/backend"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

type desktopHost struct {
	app                    *application.App
	service                *backend.Service
	tray                   *application.SystemTray
	main                   *application.WebviewWindow
	smoke                  *SmokeService
	directory              string
	mock                   bool
	assets                 fs.FS
	mu, windowMu, iconMu   sync.Mutex
	quitting, quitApproved bool
	serial                 uint
	mainReady              bool
	mainOpenPending        atomic.Bool
	pendingReport          bool
	savedMain              backend.Row
	icons                  map[string]desktopIcon
	trayLabel              string
	quitBusy               bool
}

func (h *desktopHost) language() string {
	if h.service == nil {
		return "en"
	}
	return backend.ValueString(h.service.Config()["language"])
}
func (h *desktopHost) label(zh, en string) string {
	if h.language() == "en" {
		return en
	}
	return zh
}
func (h *desktopHost) brandIcon(id string) []byte {
	path := "brand/app.ico"
	if id != "" && id != "main" && filepath.Base(id) == id && !strings.ContainsAny(id, `/\`) {
		path = "brand/app-icons/" + id + ".png"
	}
	b, e := fs.ReadFile(h.assets, path)
	if e != nil {
		b, _ = fs.ReadFile(h.assets, "brand/app.ico")
	}
	return b
}
func (h *desktopHost) setupTray() {
	if h.smoke != nil {
		return
	}
	h.tray = h.app.SystemTray.New()
	// ICO carries pre-rendered small sizes; scaling the app PNG loses tray detail.
	light, _ := fs.ReadFile(h.assets, "brand/tray-light.ico")
	dark, _ := fs.ReadFile(h.assets, "brand/tray-dark.ico")
	h.tray.SetIcon(light)
	h.tray.SetDarkModeIcon(dark)
	h.tray.SetTooltip("Codexio")
	menu := h.app.NewMenu()
	menu.Add(h.label("打开主窗口", "Open Codexio")).OnClick(func(*application.Context) { h.requestOpenMain() })
	menu.Add(h.label("AI 使用报告", "AI usage report")).OnClick(func(*application.Context) { _ = h.action("show-report") })
	menu.Add(h.label("刷新", "Refresh")).OnClick(func(*application.Context) { h.service.Refresh() })
	menu.AddSeparator()
	menu.Add(h.label("退出", "Quit")).OnClick(func(*application.Context) { go h.requestQuit() })
	h.tray.SetMenu(menu)
	h.tray.OnDoubleClick(h.requestOpenMain)
	h.tray.OnClick(h.requestOpenMain)
}
func (h *desktopHost) requestOpenMain() {
	// Wails beta.26 invokes tray clicks directly from its Win32 WndProc.
	// WebView2 creation pumps native messages while openMain holds windowMu;
	// a second click must not wait for that lock on the same native thread.
	if !h.mainOpenPending.CompareAndSwap(false, true) {
		return
	}
	go func() {
		defer h.mainOpenPending.Store(false)
		h.openMain()
	}()
}
func (h *desktopHost) openMain() {
	h.windowMu.Lock()
	defer h.windowMu.Unlock()
	h.mu.Lock()
	if h.quitting {
		h.mu.Unlock()
		return
	}
	current := h.main
	saved := backend.CloneRow(h.savedMain)
	h.mu.Unlock()
	if current != nil {
		if h.smoke != nil {
			desktopShowSmokeWindow(current)
			return
		}
		current.Show()
		current.UnMinimise()
		current.Focus()
		return
	}
	config := h.service.Config()
	if len(saved) == 0 {
		saved = desktopMainGeometry(config["main_geometry"])
	}
	width, height := 1100, 780
	if n := backend.ValueInt(saved["width"]); n > 0 {
		width = int(n)
	}
	if n := backend.ValueInt(saved["height"]); n > 0 {
		height = int(n)
	}
	url := "/"
	if h.smoke != nil {
		url = "/?smoke=1"
	}
	h.mu.Lock()
	h.serial++
	serial := h.serial
	h.mu.Unlock()
	options := application.WebviewWindowOptions{Name: desktopWindowName("main", serial), Title: "Codexio", Width: max(720, min(1800, width)), Height: max(480, min(1400, height)), MinWidth: 720, MinHeight: 480, URL: url, BackgroundColour: application.NewRGB(250, 250, 250), Windows: application.WindowsWindow{Theme: desktopTheme(config)}, DevToolsEnabled: false, DefaultContextMenuDisabled: true}
	if backend.ValueBool(saved["maximized"]) {
		options.StartState = application.WindowStateMaximised
	}
	if saved["x"] != nil && saved["y"] != nil {
		options.InitialPosition = application.WindowXY
		options.X = int(backend.ValueInt(saved["x"]))
		options.Y = int(backend.ValueInt(saved["y"]))
	}
	if h.smoke != nil {
		options.Hidden = true
		options.InitialPosition = application.WindowXY
		options.X, options.Y = desktopSmokePosition(options.Width)
		options.StartState = application.WindowStateNormal
		options.Windows.HiddenOnTaskbar = true
	}
	w := h.app.Window.NewWithOptions(options)
	h.mu.Lock()
	h.main = w
	h.mainReady = false
	h.mu.Unlock()
	if h.smoke == nil && saved["x"] != nil && saved["y"] != nil {
		w.SetPosition(int(backend.ValueInt(saved["x"])), int(backend.ValueInt(saved["y"])))
		h.clampVisible(w)
	}
	w.RegisterHook(events.Common.WindowClosing, func(*application.WindowEvent) {
		r := h.geometry(w)
		h.mu.Lock()
		if backend.ValueBool(r["maximized"]) && len(h.savedMain) > 0 {
			normal := backend.CloneRow(h.savedMain)
			normal["maximized"] = true
			r = normal
		}
		if h.main == w {
			h.main = nil
			h.mainReady = false
		}
		h.savedMain = r
		quitting := h.quitting
		h.mu.Unlock()
		if !quitting {
			go h.persist(backend.Row{"main_geometry": r})
		}
	})
	w.OnWindowEvent(events.Windows.WindowEndMove, func(*application.WindowEvent) { h.persistMain(w) })
	w.OnWindowEvent(events.Windows.WindowEndResize, func(*application.WindowEvent) { h.persistMain(w) })
	w.OnWindowEvent(events.Common.WindowDPIChanged, func(*application.WindowEvent) { h.applyWindowAppearance(w) })
	w.OnWindowEvent(events.Common.WindowRuntimeReady, func(*application.WindowEvent) {
		h.mu.Lock()
		current := h.main == w && !h.quitting
		h.mu.Unlock()
		if !current {
			return
		}
		// Runtime readiness precedes NavigationCompleted in beta.26. Show the
		// native container now; a hidden STARTUPINFO may suppress its first show.
		if h.smoke != nil {
			desktopShowSmokeWindow(w)
		} else {
			w.Show()
			if !w.IsVisible() {
				w.Show()
			}
		}
		h.clampVisible(w)
		h.applyWindowAppearance(w)
		if !w.IsMaximised() && !w.IsMinimised() {
			normal := h.geometry(w)
			h.mu.Lock()
			h.savedMain = normal
			h.mu.Unlock()
		}
		h.mu.Lock()
		if h.main != w {
			h.mu.Unlock()
			return
		}
		h.mu.Unlock()
	})
	if h.smoke == nil {
		w.Show()
	}
}
func desktopWindowName(kind string, id uint) string {
	return "Codexio-" + kind + "-" + backend.ValueString(int(id))
}
func desktopTheme(c backend.Row) application.Theme {
	switch backend.ValueString(c["theme"]) {
	case "dark":
		return application.Dark
	case "light":
		return application.Light
	default:
		return application.SystemDefault
	}
}
func desktopMainGeometry(value any) backend.Row {
	if r := backend.ValueRow(value); len(r) > 0 {
		return r
	}
	s, ok := value.(string)
	if !ok {
		return backend.Row{}
	}
	b, e := hex.DecodeString(s)
	if e != nil || len(b) < 24 || binary.BigEndian.Uint32(b) != 0x01d9d0cb {
		return backend.Row{}
	}
	n := func(offset int) int { return int(int32(binary.BigEndian.Uint32(b[offset:]))) }
	x, y, right, bottom := n(8), n(12), n(16), n(20)
	if right < x || bottom < y {
		return backend.Row{}
	}
	return backend.Row{"x": x, "y": y, "width": right - x + 1, "height": bottom - y + 1}
}
func (h *desktopHost) geometry(w *application.WebviewWindow) backend.Row {
	x, y := w.Position()
	width, height := w.Size()
	return backend.Row{"x": x, "y": y, "width": width, "height": height, "maximized": w.IsMaximised()}
}
func (h *desktopHost) persistMain(w *application.WebviewWindow) {
	h.mu.Lock()
	valid := h.main == w && !h.quitting
	h.mu.Unlock()
	if !valid || w.IsMaximised() || w.IsMinimised() {
		return
	}
	r := h.geometry(w)
	h.mu.Lock()
	h.savedMain = r
	h.mu.Unlock()
	h.persist(backend.Row{"main_geometry": r})
}
func (h *desktopHost) persist(changes backend.Row) {
	h.mu.Lock()
	quitting := h.quitting
	h.mu.Unlock()
	if !quitting {
		if _, e := h.service.SaveSettings(changes); e != nil {
			h.notice(h.label("保存窗口设置失败", "Could not save window preferences"))
		}
	}
}
func (h *desktopHost) workArea(w *application.WebviewWindow) application.Rect {
	if w != nil {
		if s, e := w.GetScreen(); e == nil && s != nil {
			return s.WorkArea
		}
	}
	if s := h.app.Screen.GetPrimary(); s != nil {
		return s.WorkArea
	}
	return application.Rect{X: 0, Y: 0, Width: 1280, Height: 800}
}
func (h *desktopHost) clampVisible(w *application.WebviewWindow) {
	if h.smoke != nil {
		return
	}
	r := w.Bounds()
	a := h.workArea(w)
	if r.X+r.Width < a.X+80 || r.X > a.X+a.Width-80 || r.Y+r.Height < a.Y+40 || r.Y > a.Y+a.Height-40 {
		w.SetPosition(a.X+max(0, (a.Width-r.Width)/2), a.Y+max(0, (a.Height-r.Height)/2))
	}
}
func (h *desktopHost) applyAppearance() {
	h.mu.Lock()
	main, quitting := h.main, h.quitting
	h.mu.Unlock()
	if main != nil && !quitting {
		h.applyWindowAppearance(main)
	}
}
func (h *desktopHost) changed(name string, row backend.Row) {
	if h.app == nil {
		return
	}
	h.app.Event.Emit(name, row)
	if name == "codexio:changed" && backend.ValueString(row["scope"]) == "quota" {
		h.updateTray()
	}
}
func (h *desktopHost) updateTray() {
	if h.tray == nil || h.service == nil {
		return
	}
	q := h.service.GetTrayState()
	week := backend.ValueRow(q["secondary"])
	caption := "Codexio"
	if n, ok := backend.ValueFloat(week["remaining_percent"]); ok {
		caption += " · " + h.label("周额度 ", "Weekly remaining ") + backend.ValueString(n) + "%"
	}
	h.mu.Lock()
	same := h.trayLabel == caption
	h.trayLabel = caption
	h.mu.Unlock()
	if !same {
		h.tray.SetTooltip(caption)
	}
}
func (h *desktopHost) notice(message string) {
	if h.app != nil {
		h.app.Event.Emit("codexio:notice", backend.Row{"message": message})
	}
}
func (h *desktopHost) action(action string) error {
	switch action {
	case "ui-ready":
		h.mu.Lock()
		h.mainReady = h.main != nil
		report := h.pendingReport && h.mainReady
		h.pendingReport = false
		h.mu.Unlock()
		if report {
			h.app.Event.Emit("codexio:show-report", backend.Row{"period": backend.PreferredReportPeriod(time.Now())})
		}
	case "show-main":
		h.openMain()
	case "close-main":
		h.mu.Lock()
		w := h.main
		h.mu.Unlock()
		if w != nil {
			w.Close()
		}
	case "apply-settings":
		go h.applyAppearance()
	case "show-report":
		h.openMain()
		h.mu.Lock()
		ready := h.mainReady
		if !ready {
			h.pendingReport = true
		}
		h.mu.Unlock()
		if ready {
			h.app.Event.Emit("codexio:show-report", backend.Row{"period": backend.PreferredReportPeriod(time.Now())})
		}
	case "open-data":
		return desktopOpenPath(h.directory)
	case "open-reports":
		path := filepath.Join(h.directory, "Reports")
		if e := os.MkdirAll(path, 0700); e != nil {
			return e
		}
		return desktopOpenPath(path)
	case "quit", "quit-for-update":
		go h.requestQuit()
	default:
		return errors.New("不支持的桌面操作")
	}
	return nil
}
func (h *desktopHost) savePNG(data []byte, name string) (string, error) {
	h.mu.Lock()
	w := h.main
	h.mu.Unlock()
	dialog := h.app.Dialog.SaveFileWithOptions(&application.SaveFileDialogOptions{Title: h.label("保存报告图片", "Save report image"), Filename: name, CanCreateDirectories: true, Filters: []application.FileFilter{{DisplayName: "PNG", Pattern: "*.png"}}})
	if w != nil {
		dialog.AttachToWindow(w)
	}
	path, e := dialog.PromptForSingleSelection()
	if e != nil {
		return "", e
	}
	if path == "" {
		return "", errors.New("已取消保存")
	}
	if !strings.EqualFold(filepath.Ext(path), ".png") {
		path += ".png"
	}
	temporary := path + ".Codexio.tmp"
	if e = os.WriteFile(temporary, data, 0600); e != nil {
		return "", e
	}
	if e = os.Rename(temporary, path); e != nil {
		os.Remove(temporary)
		return "", e
	}
	return path, nil
}
func (h *desktopHost) shouldQuit() bool {
	h.mu.Lock()
	approved := h.quitApproved
	h.mu.Unlock()
	if approved || h.service == nil {
		return true
	}
	go h.requestQuit()
	return false
}
func (h *desktopHost) requestQuit() {
	h.mu.Lock()
	if h.quitBusy || h.quitting {
		h.mu.Unlock()
		return
	}
	h.quitBusy = true
	h.mu.Unlock()
	if e := h.service.PrepareQuit(); e != nil {
		h.mu.Lock()
		h.quitBusy = false
		h.mu.Unlock()
		h.notice(e.Error())
		return
	}
	h.mu.Lock()
	h.quitting = true
	h.quitApproved = true
	h.mu.Unlock()
	h.app.Quit()
}
func (h *desktopHost) shutdown() {
	h.mu.Lock()
	h.quitting = true
	main := h.main
	saved := backend.CloneRow(h.savedMain)
	h.mu.Unlock()
	if main != nil && !main.IsMinimised() && !main.IsMaximised() {
		saved = h.geometry(main)
	}
	if main != nil {
		saved["maximized"] = main.IsMaximised()
	}
	if len(saved) > 0 {
		_ = backend.SaveConfig(h.directory, backend.Row{"main_geometry": saved})
	}
	if h.service != nil {
		_ = h.service.Close()
	}
}
