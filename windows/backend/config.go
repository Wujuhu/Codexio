package backend

// Preferences retain the existing two-file format. Authentication never belongs
// here; PublicConfig is the only preference projection sent to a webview.
import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

var systemConfigMu sync.Mutex
var systemSettingDefaults = Row{
	"refresh_interval_seconds": 60, "display_mode": "top", "window_x": nil, "window_y": nil, "window_width": nil, "window_height": nil,
	"visual_style": "classic", "background_transparent": true, "background_opacity": 70, "show_border": true, "border_color": "#8AB4F8",
	"dock_edge": "none", "dock_top_width": nil, "dock_top_height": nil, "dock_side_width": nil, "dock_side_height": nil, "quota_scope": "auto", "codex_path": nil,
}

func systemDefaultConfig() Row {
	r := CloneRow(systemSettingDefaults)
	for k, v := range (Row{"version": 1, "theme": "system", "widget_visible": true, "auto_sync_prices": true, "auto_update": true, "macos_auto_update": true,
		"usage_report_auto": true, "usage_report_style": "garden", "app_icon": "main", "mobile_sync_enabled": false, "upstream_detection_enabled": false,
		"usage_refresh_interval_seconds": 10, "usage_refresh_interval_user_set": false, "week_estimate_interval_minutes": 30, "week_estimate_interval_version": 2,
		"codex_roots": []string{systemCodexRoot()}, "account_since": UTCStamp(time.Now()), "main_geometry": nil,
		"navigation_order": []string{"overview", "logs", "trends", "subscription", "pricing", "settings"}, "navigation_order_version": 1,
		"sidebar_collapsed": false, "sidebar_width": 238, "log_preview_width": 240, "native_log_columns": []string{"content", "model", "input", "output", "cost", "duration", "details"}, "subscription_profile": Row{"plan": "", "price_usd": nil, "renewal_date": ""}, "language": systemLanguage()}) {
		r[k] = v
	}
	return r
}

// ResolveDataDirectory preserves live legacy installations instead of moving them.
func ResolveDataDirectory(explicit string) (string, error) {
	if explicit == "" {
		explicit = os.Getenv("CODEXIO_DATA_DIR")
	}
	if explicit != "" {
		p, e := filepath.Abs(systemExpandPath(explicit))
		if e != nil {
			return "", e
		}
		return p, os.MkdirAll(p, 0700)
	}
	root := os.Getenv("LOCALAPPDATA")
	if root == "" {
		h, e := os.UserHomeDir()
		if e != nil {
			return "", e
		}
		root = filepath.Join(h, "AppData", "Local")
	}
	has := func(p string) bool {
		for _, name := range []string{"settings.json", "analytics_settings.json", "usage.sqlite"} {
			if s, e := os.Stat(filepath.Join(p, name)); e == nil && !s.IsDir() {
				return true
			}
		}
		return false
	}
	p := filepath.Join(root, "Codexio")
	if !has(p) {
		for _, n := range []string{"AIQuotaWidget", "AIQuota"} {
			old := filepath.Join(root, n)
			if has(old) {
				return old, nil
			}
		}
	}
	return p, os.MkdirAll(p, 0700)
}
func systemReadJSON(path string, limit int64) (Row, error) {
	s, e := os.Stat(path)
	if e != nil {
		return Row{}, e
	}
	if s.Size() > limit {
		return Row{}, errors.New("JSON file exceeds size limit")
	}
	f, e := os.Open(path)
	if e != nil {
		return Row{}, e
	}
	defer f.Close()
	b, e := io.ReadAll(io.LimitReader(f, limit+1))
	if e != nil {
		return Row{}, e
	}
	if int64(len(b)) > limit {
		return Row{}, errors.New("JSON file exceeds size limit")
	}
	return DecodeRow(b)
}
func LoadConfig(directory string) (Row, error) {
	systemConfigMu.Lock()
	defer systemConfigMu.Unlock()
	r := systemDefaultConfig()
	var first error
	for _, name := range []string{"settings.json", "analytics_settings.json"} {
		raw, e := systemReadJSON(filepath.Join(directory, name), 4<<20)
		if e != nil && !os.IsNotExist(e) {
			first = e
		}
		for k, v := range raw {
			r[k] = v
		}
		if name == "analytics_settings.json" && len(raw) > 0 {
			if ValueInt(raw["navigation_order_version"]) != 1 {
				r["navigation_order"] = systemDefaultConfig()["navigation_order"]
			}
			if ValueInt(raw["week_estimate_interval_version"]) != 2 && ValueInt(raw["week_estimate_interval_minutes"]) == 10 {
				r["week_estimate_interval_minutes"] = 30
			}
		}
	}
	return systemNormalizeConfig(r), first
}

// Unknown saved keys remain on disk, but a webview cannot introduce new keys or
// overwrite credentials. Update only the established public preference surface.
func SaveConfig(directory string, changes Row) error {
	systemConfigMu.Lock()
	defer systemConfigMu.Unlock()
	files := []string{"settings.json", "analytics_settings.json"}
	raws := make([]Row, 2)
	merged := systemDefaultConfig()
	for i, n := range files {
		r, e := systemReadJSON(filepath.Join(directory, n), 4<<20)
		if e != nil && !os.IsNotExist(e) {
			return e
		}
		raws[i] = r
		for k, v := range r {
			merged[k] = v
		}
	}
	for k, v := range PublicConfig(changes) {
		merged[k] = v
	}
	merged = systemNormalizeConfig(merged)
	for k, v := range PublicConfig(merged) {
		if _, ok := systemSettingDefaults[k]; ok {
			raws[0][k] = v
		} else {
			raws[1][k] = v
		}
	}
	raws[0]["version"] = 1
	raws[1]["version"] = 1
	for i, n := range files {
		if e := WriteJSON(filepath.Join(directory, n), raws[i]); e != nil {
			return e
		}
	}
	return nil
}
func PublicConfig(r Row) Row {
	out := Row{}
	defaults := systemDefaultConfig()
	for k, v := range r {
		_, known := defaults[k]
		if known || k == "column_widths" || k == "log_column_widths" || k == "tray_fields" || k == "floating_visible" || k == "floating_geometry" || k == "report_deferred_version" {
			out[k] = v
		}
	}
	return CloneRow(out)
}
func systemNormalizeConfig(r Row) Row {
	// These are the persisted palette identities from usage_report_ui.py.
	if v := ValueString(r["usage_report_style"]); v == "cream" {
		r["usage_report_style"] = "bookmark"
	} else if v == "rose" {
		r["usage_report_style"] = "afternoon"
	}
	choices := map[string][]string{"theme": {"system", "light", "dark"}, "display_mode": {"bottom", "top", "tray"}, "visual_style": {"classic", "rings", "tiles", "compact", "minimal", "orb"}, "dock_edge": {"none", "top", "bottom", "left", "right"}, "quota_scope": {"auto", "both", "week"}, "usage_report_style": {"garden", "bookmark", "afternoon"}, "language": {"zh", "en"}}
	defaults := systemDefaultConfig()
	for k, c := range choices {
		if !systemContains(c, ValueString(r[k])) {
			r[k] = defaults[k]
		}
	}
	for k, c := range map[string][]int64{"refresh_interval_seconds": {30, 60, 300}, "usage_refresh_interval_seconds": {5, 10, 30, 60}, "week_estimate_interval_minutes": {10, 30, 60}} {
		found := false
		for _, n := range c {
			found = found || ValueInt(r[k]) == n
		}
		if !found {
			r[k] = defaults[k]
		}
	}
	if !ValueBool(r["usage_refresh_interval_user_set"]) && ValueInt(r["usage_refresh_interval_seconds"]) == 5 {
		r["usage_refresh_interval_seconds"] = 10
	}
	for k := range defaults {
		if _, isBool := defaults[k].(bool); isBool {
			if _, ok := r[k].(bool); !ok {
				r[k] = defaults[k]
			}
		}
	}
	for k, b := range map[string][2]int64{"background_opacity": {0, 100}, "window_width": {200, 1200}, "window_height": {96, 800}, "dock_top_width": {40, 1200}, "dock_top_height": {32, 800}, "dock_side_width": {40, 1200}, "dock_side_height": {32, 800}, "sidebar_width": {140, 320}, "log_preview_width": {220, 480}} {
		if r[k] == nil {
			continue
		}
		n, ok := ValueFloat(r[k])
		if !ok {
			r[k] = defaults[k]
		} else {
			r[k] = max(b[0], min(b[1], int64(n)))
		}
	}
	if !regexp.MustCompile(`^#[0-9a-fA-F]{6}$`).MatchString(ValueString(r["border_color"])) {
		r["border_color"] = "#8AB4F8"
	} else {
		r["border_color"] = strings.ToUpper(ValueString(r["border_color"]))
	}
	path := strings.TrimSpace(ValueString(r["codex_path"]))
	if path == "" {
		r["codex_path"] = nil
	} else {
		r["codex_path"] = path
	}
	nav := []string{"overview"}
	for _, s := range append(ValueStrings(r["navigation_order"]), ValueStrings(defaults["navigation_order"])...) {
		if systemContains(ValueStrings(defaults["navigation_order"]), s) && !systemContains(nav, s) {
			nav = append(nav, s)
		}
	}
	r["navigation_order"] = nav
	columns := []string{"content"}
	selected := ValueStrings(r["native_log_columns"])
	for _, field := range []string{"time", "model", "input", "output", "total", "cached", "cache_write", "cache_rate", "cost", "duration", "effort", "speed", "context", "status"} {
		if systemContains(selected, field) {
			columns = append(columns, field)
		}
	}
	r["native_log_columns"] = append(columns, "details")
	roots := []string{}
	for _, s := range ValueStrings(r["codex_roots"]) {
		s = strings.TrimSpace(s)
		if s != "" && !systemContains(roots, s) {
			roots = append(roots, s)
		}
	}
	if len(roots) == 0 {
		roots = []string{systemCodexRoot()}
	}
	r["codex_roots"] = roots
	p := ValueRow(r["subscription_profile"])
	plan := []rune(strings.TrimSpace(ValueString(p["plan"])))
	if len(plan) > 64 {
		plan = plan[:64]
	}
	profile := Row{"plan": string(plan), "price_usd": nil, "renewal_date": ""}
	if n, ok := ValueFloat(p["price_usd"]); ok && n >= 0 && n <= 1e6 {
		profile["price_usd"] = n
	}
	if t, e := time.Parse("2006-01-02", ValueString(p["renewal_date"])); e == nil {
		profile["renewal_date"] = t.Format("2006-01-02")
	}
	r["subscription_profile"] = profile
	if _, ok := ParseStamp(r["account_since"]); !ok {
		r["account_since"] = defaults["account_since"]
	}
	if ValueString(r["app_icon"]) == "" {
		r["app_icon"] = "main"
	}
	r["auto_sync_prices"] = true
	r["week_estimate_interval_version"] = 2
	r["navigation_order_version"] = 1
	return r
}
func systemContains(a []string, s string) bool {
	for _, v := range a {
		if v == s {
			return true
		}
	}
	return false
}
func systemExpandPath(p string) string {
	p = strings.Trim(strings.TrimSpace(p), `"`)
	p = os.ExpandEnv(p)
	p = regexp.MustCompile(`%([^%]+)%`).ReplaceAllStringFunc(p, func(s string) string {
		if v := os.Getenv(s[1 : len(s)-1]); v != "" {
			return v
		}
		return s
	})
	if p == "~" || strings.HasPrefix(p, "~/") || strings.HasPrefix(p, `~\`) {
		h, _ := os.UserHomeDir()
		p = filepath.Join(h, strings.TrimLeft(p[1:], `/\`))
	}
	return p
}
func systemCodexRoot() string {
	if r := os.Getenv("CODEX_HOME"); r != "" {
		return systemExpandPath(r)
	}
	h, _ := os.UserHomeDir()
	return filepath.Join(h, ".codex")
}
