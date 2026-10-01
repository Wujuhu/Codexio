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
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

type desktopHost struct {
	app                      *application.App
	service                  *backend.Service
	tray                     *application.SystemTray
	main, floating           *application.WebviewWindow
	smoke                    *SmokeService
	directory                string
	mock                     bool
	assets                   fs.FS
	mu, windowMu, iconMu     sync.Mutex
	quitting, quitApproved   bool
	serial                   uint
	mainReady                bool
	pendingReport            bool
	savedMain, savedFloating backend.Row
	icons                    map[string]desktopIcon
	trayLabel                string
	quitBusy                 bool
	floatingOrigin           application.Rect
	floatingInteracting      bool
	floatingLayout           string
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
	path := "brand/app.png"
	if id != "" && id != "main" && filepath.Base(id) == id && !strings.ContainsAny(id, `/\`) {
		path = "brand/app-icons/" + id + ".png"
	}
	b, e := fs.ReadFile(h.assets, path)
	if e != nil {
		b, _ = fs.ReadFile(h.assets, "brand/app.png")
	}
	return b
}
func (h *desktopHost) setupTray() {
	h.tray = h.app.SystemTray.New()
	h.tray.SetIcon(h.brandIcon("main"))
	h.tray.SetTooltip("Codexio")
	menu := h.app.NewMenu()
	menu.Add(h.label("打开主窗口", "Open Codexio")).OnClick(func(*application.Context) { h.openMain() })
	menu.Add(h.label("显示／隐藏悬浮窗", "Show / hide floating window")).OnClick(func(*application.Context) { _ = h.action("toggle-floating") })
	menu.Add(h.label("AI 使用报告", "AI usage report")).OnClick(func(*application.Context) { _ = h.action("show-report") })
	menu.Add(h.label("刷新", "Refresh")).OnClick(func(*application.Context) { h.service.Refresh() })
	menu.AddSeparator()
	menu.Add(h.label("退出", "Quit")).OnClick(func(*application.Context) { go h.requestQuit() })
	h.tray.SetMenu(menu)
	h.tray.OnDoubleClick(h.openMain)
	h.tray.OnClick(h.openMain)
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
	w := h.app.Window.NewWithOptions(options)
	h.mu.Lock()
	h.main = w
	h.mainReady = false
	h.mu.Unlock()
	if saved["x"] != nil && saved["y"] != nil {
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
	w.OnWindowEvent(events.Common.WindowRuntimeReady, func(*application.WindowEvent) {
		h.mu.Lock()
		current := h.main == w && !h.quitting
		h.mu.Unlock()
		if !current {
			return
		}
		// Runtime readiness precedes NavigationCompleted in beta.26. Show the
		// native container now; a hidden STARTUPINFO may suppress its first show.
		w.Show()
		if !w.IsVisible() {
			w.Show()
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
	w.Show()
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
	r := w.Bounds()
	a := h.workArea(w)
	if r.X+r.Width < a.X+80 || r.X > a.X+a.Width-80 || r.Y+r.Height < a.Y+40 || r.Y > a.Y+a.Height-40 {
		w.SetPosition(a.X+max(0, (a.Width-r.Width)/2), a.Y+max(0, (a.Height-r.Height)/2))
	}
}
func (h *desktopHost) openFloating() {
	h.windowMu.Lock()
	defer h.windowMu.Unlock()
	h.mu.Lock()
	if h.quitting {
		h.mu.Unlock()
		return
	}
	existing := h.floating
	h.mu.Unlock()
	if existing != nil {
		existing.Show()
		h.positionFloating(existing, h.service.Config())
		return
	}
	c := h.floatingConfig(h.service.Config())
	width, height := desktopFloatingSize(c)
	w := h.app.Window.NewWithOptions(application.WebviewWindowOptions{Name: "Codexio-floating", Title: "Codexio", URL: "/?view=floating", Width: width, Height: height, MinWidth: 40, MinHeight: 32, MaxWidth: 1200, MaxHeight: 800, Frameless: true, AlwaysOnTop: backend.ValueString(c["display_mode"]) != "bottom", BackgroundType: application.BackgroundTypeTransparent, BackgroundColour: application.NewRGBA(0, 0, 0, 0), Windows: application.WindowsWindow{HiddenOnTaskbar: true, Theme: application.Dark, DisableFramelessWindowDecorations: true, NonClientRegionSupport: true}, DevToolsEnabled: false, DefaultContextMenuDisabled: true})
	h.mu.Lock()
	h.floating = w
	h.mu.Unlock()
	h.positionFloating(w, c)
	w.RegisterHook(events.Common.WindowClosing, func(*application.WindowEvent) {
		h.mu.Lock()
		if h.floating == w {
			h.floating = nil
			h.floatingLayout = ""
		}
		quitting := h.quitting
		h.mu.Unlock()
		config := h.service.Config()
		if !quitting && backend.ValueBool(config["widget_visible"]) && backend.ValueString(config["display_mode"]) != "tray" {
			go h.persist(backend.Row{"widget_visible": false})
		}
	})
	end := func(*application.WindowEvent) { h.endFloatingInteraction(w) }
	begin := func(*application.WindowEvent) {
		r := w.Bounds()
		h.mu.Lock()
		h.floatingOrigin = r
		h.floatingInteracting = true
		h.mu.Unlock()
	}
	w.OnWindowEvent(events.Windows.WindowStartMove, begin)
	w.OnWindowEvent(events.Windows.WindowStartResize, begin)
	w.OnWindowEvent(events.Windows.WindowEndMove, end)
	w.OnWindowEvent(events.Windows.WindowEndResize, end)
	w.OnWindowEvent(events.Common.WindowRuntimeReady, func(*application.WindowEvent) {
		h.mu.Lock()
		current := h.floating == w && !h.quitting
		h.mu.Unlock()
		if !current {
			return
		}
		w.Show()
		if !w.IsVisible() {
			w.Show()
		}
		h.applyWindowAppearance(w)
		w.SetResizable(true)
		h.positionFloating(w, h.service.Config())
	})
	w.OnWindowEvent(events.Common.WindowFocus, func(*application.WindowEvent) {
		if backend.ValueString(h.service.Config()["display_mode"]) == "bottom" {
			desktopPlaceBottom(w)
		}
	})
	w.Show()
	if backend.ValueString(c["display_mode"]) == "bottom" {
		desktopPlaceBottom(w)
	}
}
func desktopFloatingSize(c backend.Row) (int, int) {
	week := backend.ValueString(c["quota_scope"]) == "week" || backend.ValueBool(c["_week_only"])
	sizes := map[string][2]int{"classic": {304, 208}, "rings": {328, 212}, "tiles": {336, 160}, "compact": {320, 132}, "minimal": {276, 96}, "orb": {148, 148}}
	weekSizes := map[string][2]int{"classic": {272, 136}, "rings": {196, 204}, "tiles": {240, 160}, "compact": {228, 132}, "minimal": {160, 88}, "orb": {148, 148}}
	if week {
		sizes = weekSizes
	}
	size, ok := sizes[backend.ValueString(c["visual_style"])]
	if !ok {
		size = sizes["classic"]
	}
	width, height := size[0], size[1]
	edge := backend.ValueString(c["dock_edge"])
	if edge == "" || edge == "none" {
		if n := backend.ValueInt(c["window_width"]); n > 0 {
			width = int(n)
		}
		if n := backend.ValueInt(c["window_height"]); n > 0 {
			height = int(n)
		}
	}
	if backend.ValueString(c["visual_style"]) == "orb" {
		side := max(72, min(400, max(width, height)))
		width, height = side, side
	}
	switch edge {
	case "top", "bottom":
		width, height = 590, 46
		if week {
			width = 360
		}
		if n := backend.ValueInt(c["dock_top_width"]); n > 0 {
			width = int(n)
		}
		if n := backend.ValueInt(c["dock_top_height"]); n > 0 {
			height = int(n)
		}
		minimum := 420
		if week {
			minimum = 260
		}
		width = max(minimum, min(960, width))
		height = max(44, min(96, height))
	case "left", "right":
		width, height = 44, 256
		if week {
			height = 160
		}
		if n := backend.ValueInt(c["dock_side_width"]); n > 0 {
			width = int(n)
		}
		if n := backend.ValueInt(c["dock_side_height"]); n > 0 {
			height = int(n)
		}
		minimum := 196
		if week {
			minimum = 124
		}
		width = max(36, min(120, width))
		height = max(minimum, min(720, height))
	}
	minWidth, minHeight, maxWidth, maxHeight := desktopFloatingLimits(c)
	return max(minWidth, min(maxWidth, width)), max(minHeight, min(maxHeight, height))
}
func desktopFloatingLimits(c backend.Row) (int, int, int, int) {
	week := backend.ValueString(c["quota_scope"]) == "week" || backend.ValueBool(c["_week_only"])
	switch backend.ValueString(c["dock_edge"]) {
	case "top", "bottom":
		minimum := 420
		if week {
			minimum = 260
		}
		return minimum, 44, 960, 96
	case "left", "right":
		minimum := 196
		if week {
			minimum = 124
		}
		return 36, minimum, 120, 720
	}
	if backend.ValueString(c["visual_style"]) == "orb" {
		return 100, 100, 400, 400
	}
	minimum := map[string][2]int{"classic": {240, 196}, "rings": {288, 182}, "tiles": {288, 160}, "compact": {280, 132}, "minimal": {240, 88}}
	if week {
		minimum = map[string][2]int{"classic": {228, 124}, "rings": {164, 182}, "tiles": {180, 160}, "compact": {180, 132}, "minimal": {130, 88}}
	}
	size, ok := minimum[backend.ValueString(c["visual_style"])]
	if !ok {
		size = minimum["classic"]
	}
	return size[0], size[1], 1200, 800
}

func desktopFloatingLayoutKey(c backend.Row) string {
	return backend.ValueString(c["visual_style"]) + ":" + backend.ValueString(c["dock_edge"]) + ":" + backend.ValueString(c["_week_only"]) + ":" + backend.ValueString(c["quota_scope"])
}
func (h *desktopHost) floatingConfig(c backend.Row) backend.Row {
	c = backend.CloneRow(c)
	if backend.ValueString(c["quota_scope"]) == "auto" {
		primary := backend.ValueRow(h.service.GetTrayState()["primary"])
		_, remaining := backend.ValueFloat(primary["remaining_percent"])
		_, used := backend.ValueFloat(primary["used_percent"])
		c["_week_only"] = !remaining && !used
	}
	// Saved dimensions belong to a layout, including the automatic quota count.
	if backend.ValueString(c["floating_geometry_key"]) != desktopFloatingLayoutKey(c) {
		for _, key := range []string{"window_width", "window_height", "dock_top_width", "dock_top_height", "dock_side_width", "dock_side_height"} {
			c[key] = nil
		}
	}
	return c
}
func (h *desktopHost) positionFloating(w *application.WebviewWindow, c backend.Row) {
	c = h.floatingConfig(c)
	layout := desktopFloatingLayoutKey(c)
	h.mu.Lock()
	layoutChanged := h.floatingLayout != "" && h.floatingLayout != layout
	h.floatingLayout = layout
	h.mu.Unlock()
	if layoutChanged {
		for _, key := range []string{"window_width", "window_height", "dock_top_width", "dock_top_height", "dock_side_width", "dock_side_height"} {
			c[key] = nil
		}
	}
	width, height := desktopFloatingSize(c)
	minWidth, minHeight, maxWidth, maxHeight := desktopFloatingLimits(c)
	w.SetMinSize(minWidth, minHeight)
	w.SetMaxSize(maxWidth, maxHeight)
	area := h.workArea(w)
	width = min(width, area.Width)
	height = min(height, area.Height)
	x, y := area.X+area.Width-width-18, area.Y+area.Height-height-56
	if c["window_x"] != nil {
		x = int(backend.ValueInt(c["window_x"]))
	}
	if c["window_y"] != nil {
		y = int(backend.ValueInt(c["window_y"]))
	}
	x = max(area.X, min(area.X+area.Width-width, x))
	y = max(area.Y, min(area.Y+area.Height-height, y))
	switch backend.ValueString(c["dock_edge"]) {
	case "top":
		y = area.Y
	case "bottom":
		y = area.Y + area.Height - height
	case "left":
		x = area.X
	case "right":
		x = area.X + area.Width - width
	}
	w.SetBounds(application.Rect{X: x, Y: y, Width: width, Height: height})
	w.SetAlwaysOnTop(backend.ValueString(c["display_mode"]) != "bottom")
	if backend.ValueString(c["display_mode"]) == "bottom" {
		desktopPlaceBottom(w)
	}
}
func (h *desktopHost) endFloatingInteraction(w *application.WebviewWindow) {
	h.mu.Lock()
	valid := h.floating == w && !h.quitting
	h.mu.Unlock()
	if !valid {
		return
	}
	c := h.floatingConfig(h.service.Config())
	r := w.Bounds()
	h.mu.Lock()
	origin := h.floatingOrigin
	interacting := h.floatingInteracting
	h.floatingInteracting = false
	h.mu.Unlock()
	area := h.workArea(w)
	threshold := 24
	if backend.ValueString(c["dock_edge"]) != "none" {
		threshold = 40
	}
	type candidate struct {
		distance, priority int
		edge               string
	}
	best := candidate{threshold + 1, 2, "none"}
	for _, v := range []candidate{{max(0, r.Y-area.Y), 0, "top"}, {max(0, area.Y+area.Height-r.Y-r.Height), 0, "bottom"}, {max(0, r.X-area.X), 1, "left"}, {max(0, area.X+area.Width-r.X-r.Width), 1, "right"}} {
		if v.distance <= threshold && (v.distance < best.distance || v.distance == best.distance && v.priority < best.priority) {
			best = v
		}
	}
	edge := best.edge
	oldEdge := backend.ValueString(c["dock_edge"])
	if oldEdge == "" {
		oldEdge = "none"
	}
	if !interacting || origin.Width != r.Width || origin.Height != r.Height {
		edge = oldEdge
	}
	changes := backend.Row{"window_x": r.X, "window_y": r.Y, "dock_edge": edge}
	savedLayout := backend.CloneRow(c)
	savedLayout["dock_edge"] = edge
	changes["floating_geometry_key"] = desktopFloatingLayoutKey(savedLayout)
	if edge != oldEdge {
		// Reuse v0.2.10 dock.py:snap_geometry's center anchor. Changing a
		// horizontal bar to a vertical one must not jump to a screen corner.
		layout := backend.CloneRow(c)
		layout["dock_edge"] = edge
		for _, key := range []string{"window_width", "window_height", "dock_top_width", "dock_top_height", "dock_side_width", "dock_side_height"} {
			layout[key] = nil
		}
		layout = h.floatingConfig(layout)
		width, height := desktopFloatingSize(layout)
		changes["window_x"] = r.X + (r.Width-width)/2
		changes["window_y"] = r.Y + (r.Height-height)/2
	}
	if edge == "none" {
		if oldEdge == "none" {
			changes["window_width"] = r.Width
			changes["window_height"] = r.Height
		}
	} else if edge == "top" || edge == "bottom" {
		if backend.ValueString(c["dock_edge"]) == edge {
			changes["dock_top_width"] = r.Width
			changes["dock_top_height"] = r.Height
		}
	} else {
		if backend.ValueString(c["dock_edge"]) == edge {
			changes["dock_side_width"] = r.Width
			changes["dock_side_height"] = r.Height
		}
	}
	h.mu.Lock()
	h.savedFloating = backend.CloneRow(changes)
	h.mu.Unlock()
	h.persist(changes)
}
func (h *desktopHost) applySettings() {
	h.mu.Lock()
	if h.quitting {
		h.mu.Unlock()
		return
	}
	floating := h.floating
	h.mu.Unlock()
	c := h.service.Config()
	visible := backend.ValueBool(c["widget_visible"]) && backend.ValueString(c["display_mode"]) != "tray"
	if visible {
		if floating == nil {
			h.openFloating()
		} else {
			floating.Show()
			h.positionFloating(floating, c)
		}
	} else if floating != nil {
		floating.Close()
	}
	h.applyAppearance()
}
func (h *desktopHost) applyAppearance() {
	h.mu.Lock()
	main, floating := h.main, h.floating
	h.mu.Unlock()
	for _, w := range []*application.WebviewWindow{main, floating} {
		if w != nil {
			h.applyWindowAppearance(w)
		}
	}
}
func (h *desktopHost) changed(name string, row backend.Row) {
	if h.app == nil {
		return
	}
	h.app.Event.Emit(name, row)
	if name == "codexio:changed" && backend.ValueString(row["scope"]) == "quota" {
		h.updateTray()
		h.mu.Lock()
		floating := h.floating
		dragging := h.floatingInteracting
		h.mu.Unlock()
		if floating != nil && !dragging {
			config := h.floatingConfig(h.service.Config())
			if backend.ValueString(config["quota_scope"]) == "auto" {
				width, height := desktopFloatingSize(config)
				actualW, actualH := floating.Size()
				layout := desktopFloatingLayoutKey(config)
				h.mu.Lock()
				changed := h.floatingLayout != layout
				h.mu.Unlock()
				if changed || width != actualW || height != actualH {
					h.positionFloating(floating, config)
				}
			}
		}
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
	case "toggle-floating":
		config := h.service.Config()
		visible := !backend.ValueBool(config["widget_visible"]) || backend.ValueString(config["display_mode"]) == "tray"
		changes := backend.Row{"widget_visible": visible}
		if visible && backend.ValueString(h.service.Config()["display_mode"]) == "tray" {
			changes["display_mode"] = "top"
		}
		if _, e := h.service.SaveSettings(changes); e != nil {
			return e
		}
	case "hide-floating":
		_, e := h.service.SaveSettings(backend.Row{"widget_visible": false})
		return e
	case "apply-settings":
		go h.applySettings()
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
	main, floating := h.main, h.floating
	saved := backend.CloneRow(h.savedMain)
	changes := backend.CloneRow(h.savedFloating)
	h.mu.Unlock()
	if main != nil && !main.IsMinimised() && !main.IsMaximised() {
		saved = h.geometry(main)
	}
	if main != nil {
		saved["maximized"] = main.IsMaximised()
	}
	if len(saved) > 0 {
		changes["main_geometry"] = saved
	}
	if floating != nil {
		r := floating.Bounds()
		changes["window_x"] = r.X
		changes["window_y"] = r.Y
	}
	if len(changes) > 0 {
		_ = backend.SaveConfig(h.directory, changes)
	}
	if h.service != nil {
		_ = h.service.Close()
	}
}
