package backend

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Service is the only public desktop bridge. Components own their IO and
// background work; a webview receives pages and projections, never a live DB.
type Service struct {
	mu               sync.RWMutex
	config           Row
	directory        string
	version          string
	mock             bool
	store            *Store
	quota            *QuotaService
	updater          *Updater
	reports          *ReportManager
	estimate         *RollingEstimator
	upstream         *UpstreamProxy
	mobile           *MobileHost
	callbacks        DesktopCallbacks
	ctx              context.Context
	cancel           context.CancelFunc
	closeOnce        sync.Once
	startOnce        sync.Once
	reportMu         sync.Mutex
	reportBusy       bool
	reportGeneration int64
	reportRead       time.Time
	reportSignature  string
	reportAccount    string
}

func NewService(directory, executable, version string, mock bool, callbacks DesktopCallbacks) (*Service, error) {
	c, err := LoadConfig(directory)
	if err != nil {
		return nil, fmt.Errorf("读取设置失败，原文件已保留: %w", err)
	}
	if mock {
		c["auto_update"] = false
		c["mobile_sync_enabled"] = false
		c["upstream_detection_enabled"] = false
		c["auto_sync_prices"] = false
		c["widget_visible"] = false
	}
	ctx, cancel := context.WithCancel(context.Background())
	s := &Service{config: c, directory: directory, version: version, mock: mock, callbacks: callbacks, ctx: ctx, cancel: cancel}
	s.store, err = OpenStore(DataOptions{Directory: directory, Roots: ValueStrings(c["codex_roots"]), Mock: mock, Config: CloneRow(c), ScanUpdated: func(stamp string) {
		if callbacks.Changed != nil {
			callbacks.Changed("codexio:clock", Row{"updated_at": stamp})
		}
	}})
	if err != nil {
		cancel()
		return nil, err
	}
	s.estimate, err = NewRollingEstimator(s.store, s.Config, func() { s.notify("subscription") })
	if err != nil {
		s.store.Close()
		cancel()
		return nil, err
	}
	opts := SystemOptions{Directory: directory, Executable: executable, Version: version, Mock: mock, Config: s.Config, Changed: func() { s.notify("quota") }, Exit: func() {
		if callbacks.Action != nil {
			_ = callbacks.Action("quit-for-update")
		}
	}}
	opts.Sample = func(r Row) {
		if !mock {
			s.estimate.Sample(r)
		}
	}
	s.quota = NewQuota(opts)
	opts.Sample = nil
	opts.Changed = func() { s.notify("update") }
	s.updater = NewUpdater(opts)
	s.reports = NewReportManager(s.store, directory, s.Config)
	s.upstream = NewUpstreamProxy(directory, version, s.Config, mock, func() { s.notify("upstream") })
	s.mobile = NewMobileHost(directory, s.store, s.quota, s.Config, mock, func() { s.notify("mobile") })
	return s, nil
}

func (s *Service) Config() Row { s.mu.RLock(); defer s.mu.RUnlock(); return PublicConfig(s.config) }
func (s *Service) notify(scope string) {
	if s.callbacks.Changed != nil {
		event := Row{"scope": scope, "generation": s.store.Generation()}
		if scope == "usage" {
			event["state"] = s.store.Status()
		}
		s.callbacks.Changed("codexio:changed", event)
	}
}
func (s *Service) Start() {
	s.startOnce.Do(func() {
		go s.store.Run(s.ctx, func() {
			if !s.mock {
				s.estimate.Process()
			}
			s.notify("usage")
			s.mobile.RequestProjection()
		})
		s.quota.Start(s.ctx)
		s.updater.Start(s.ctx)
		s.mobile.Start(s.ctx)
		if !s.mock {
			go func() {
				if _, e := s.upstream.Toggle(ValueBool(s.Config()["upstream_detection_enabled"])); e != nil {
					s.notice(e.Error())
				}
			}()
		}
	})
}
func (s *Service) notice(message string) {
	if s.callbacks.Changed != nil {
		s.callbacks.Changed("codexio:notice", Row{"message": message})
	}
}

func (s *Service) GetOverview(q Query) (Row, error) {
	summary, e := s.store.Summary(q)
	if e != nil {
		return nil, e
	}
	chart, e := s.store.Chart(q)
	if e != nil {
		return nil, e
	}
	recent, e := s.store.Recent(q, 3)
	if e != nil {
		return nil, e
	}
	models, e := s.store.Models(q)
	if e != nil {
		return nil, e
	}
	return Row{"summary": summary, "chart": chart, "recent": recent, "models": models, "quota": s.subscription(false), "state": s.store.Status(), "generation": s.store.Generation()}, nil
}
func (s *Service) GetLogs(q Query) (PageResult, error)         { return s.store.Page(q) }
func (s *Service) GetState() Row                               { return s.store.Status() }
func (s *Service) GetDetails(id string, page int) (Row, error) { return s.store.Detail(id, page) }
func (s *Service) GetTrends(q Query) (Row, error) {
	summary, e := s.store.Summary(q)
	if e != nil {
		return nil, e
	}
	chart, e := s.store.Chart(q)
	if e != nil {
		return nil, e
	}
	models, e := s.store.Models(q)
	if e != nil {
		return nil, e
	}
	chatQuery := Query{Period: "all", Source: "local", Page: q.Page, PageSize: 25}
	insights, e := s.store.Insights(chatQuery)
	if e != nil {
		return nil, e
	}
	s.requestAccountReports(false)
	return Row{"summary": summary, "chart": chart, "models": models, "insights": insights, "heatmap": insights["heatmap"], "activity": Row{"rows": insights["activity"]}, "chats": insights["chats"], "chat_page": insights["chat_page"], "chat_pages": insights["chat_pages"], "chat_total": insights["chat_total"], "chat_usage": s.GetChatRanking("weekly_limit_percent", 1)}, nil
}
func (s *Service) GetSubscription() Row { return s.subscription(true) }
func (s *Service) GetTrayState() Row {
	r := s.quota.Snapshot()
	delete(r, "reports")
	r["state"] = s.store.Status()
	return r
}
func (s *Service) subscription(reports bool) Row {
	r := s.quota.Snapshot()
	if r == nil {
		r = Row{"status": "loading"}
	}
	if r["credits"] == nil {
		r["credits"] = r["reset_credit_details"]
	}
	if !reports {
		delete(r, "reports")
		return r
	}
	s.requestAccountReports(false)
	s.projectAccountReports(r)
	r["subscription_profile"] = s.Config()["subscription_profile"]
	r["rolling_estimates"] = s.estimate.Rows(ValueString(r["account_key"]))
	r["estimate"] = Row{"usd": nil, "status": "等待有效额度观察区间"}
	if rows := ValueRows(r["rolling_estimates"]); len(rows) > 0 {
		r["estimate"] = Row{"usd": rows[0]["estimated_total_usd"], "status": "参考估值", "start": rows[0]["start"], "end": rows[0]["end"]}
	}
	return r
}
func (s *Service) GetPrices() Row {
	st := s.store.Status()
	return Row{"rows": codexDisplayPrices(s.store.Prices()), "basis": "标准 API 单价 × Codex 倍率", "version": st["price_version"], "status": st["pricing"]}
}
func (s *Service) SavePrice(model string, rates Row) (Row, error) {
	if e := s.store.SetPrice(model, rates); e != nil {
		return nil, e
	}
	s.notify("pricing")
	return s.GetPrices(), nil
}
func (s *Service) SyncPrices() (Row, error) {
	if e := s.store.RefreshPrices(s.ctx); e != nil {
		return s.GetPrices(), e
	}
	s.notify("pricing")
	return s.GetPrices(), nil
}
func (s *Service) GetReports(period string) (Row, error) { return s.reports.Get(period) }

func (s *Service) GetSettings() Row {
	r := PublicConfig(s.Config())
	r["data_path"] = s.directory
	r["version"] = s.version
	r["mock"] = s.mock
	r["state"] = s.store.Status()
	r["mobile"] = s.mobile.Public()
	r["upstream"] = s.upstream.Public()
	r["update"] = s.updater.Snapshot()
	r["report_due"] = s.reports.Due()
	r["report_preferred_period"] = PreferredReportPeriod(time.Now())
	return r
}
func normalizeFloatingChanges(old, changes Row) Row {
	next := CloneRow(changes)
	next["floating_layout_version"] = 1
	changed := func(key string) bool {
		value, present := changes[key]
		return present && dataJSON(value) != dataJSON(old[key])
	}
	clearFree := func() { next["window_width"], next["window_height"] = nil, nil }
	clearHorizontal := func() { next["dock_top_width"], next["dock_top_height"] = nil, nil }
	clearVertical := func() { next["dock_side_width"], next["dock_side_height"] = nil, nil }
	if changed("visual_style") {
		clearFree()
	}
	if changed("quota_scope") {
		clearFree()
		clearHorizontal()
		clearVertical()
	}
	if changed("dock_edge") {
		oldEdge, newEdge := ValueString(old["dock_edge"]), ValueString(changes["dock_edge"])
		if oldEdge == "" {
			oldEdge = "none"
		}
		if newEdge == "" {
			newEdge = "none"
		}
		if newEdge == "none" {
			clearFree()
		} else if newEdge == "top" || newEdge == "bottom" {
			if oldEdge == "none" || oldEdge == "left" || oldEdge == "right" {
				clearHorizontal()
			}
		} else if oldEdge == "none" || oldEdge == "top" || oldEdge == "bottom" {
			clearVertical()
		}
	}
	return next
}
func (s *Service) SaveSettings(changes Row) (Row, error) {
	if s.mock && (ValueBool(changes["upstream_detection_enabled"]) || ValueBool(changes["mobile_sync_enabled"])) {
		return nil, errors.New("模拟模式不会修改真实服务或 Codex 配置")
	}
	old := s.Config()
	if desired, present := changes["upstream_detection_enabled"]; present {
		delete(changes, "upstream_detection_enabled")
		if ValueBool(desired) != ValueBool(old["upstream_detection_enabled"]) {
			if _, e := s.UpstreamAction(ValueBool(desired)); e != nil {
				return nil, e
			}
			old = s.Config()
		}
	}
	changes = normalizeFloatingChanges(old, changes)
	if e := SaveConfig(s.directory, changes); e != nil {
		return nil, e
	}
	c, e := LoadConfig(s.directory)
	if e != nil {
		return nil, e
	}
	if s.mock {
		c["auto_update"] = false
		c["mobile_sync_enabled"] = false
		c["upstream_detection_enabled"] = false
		c["auto_sync_prices"] = false
		c["widget_visible"] = false
	}
	s.mu.Lock()
	s.config = c
	s.mu.Unlock()
	s.store.Configure(c)
	if ValueBool(old["mobile_sync_enabled"]) != ValueBool(c["mobile_sync_enabled"]) {
		if _, e = s.mobile.Action("enable", Row{"enabled": ValueBool(c["mobile_sync_enabled"])}); e != nil {
			s.notice(e.Error())
		}
	}
	if s.callbacks.Action != nil {
		_ = s.callbacks.Action("apply-settings")
	}
	s.notify("settings")
	return s.GetSettings(), nil
}
func (s *Service) Refresh() {
	s.store.Refresh()
	s.quota.Refresh()
	s.requestAccountReports(true)
	s.mobile.RequestProjection()
}
func (s *Service) Rescan()                            { s.store.Rescan() }
func (s *Service) CheckUpdate() (Row, error)          { return s.updater.Check(s.ctx) }
func (s *Service) DeferUpdate() (Row, error)          { e := s.updater.Defer(); return s.updater.Snapshot(), e }
func (s *Service) InstallUpdate() error               { return s.updater.Install(s.ctx) }
func (s *Service) ResetCredit(id string) (Row, error) { return s.quota.ResetCredit(id) }
func (s *Service) ResetCreditForAccount(id, expectedAccount string) (Row, error) {
	return s.quota.ResetCreditForAccount(id, expectedAccount)
}
func (s *Service) DesktopAction(action string) error {
	if action == "report-seen" {
		return s.reports.MarkSeen()
	}
	if action == "restart-client" && s.mock {
		return errors.New("模拟模式不会重启真实客户端")
	}
	if action == "restart-client" {
		return s.upstream.RestartClient()
	}
	if s.callbacks.Action == nil {
		return errors.New("桌面窗口尚未准备好")
	}
	return s.callbacks.Action(action)
}
func (s *Service) OpenURL(raw string) error {
	u, e := url.Parse(raw)
	if e != nil || u.Host == "" || u.User != nil || (u.Scheme != "https" && u.Scheme != "http") {
		return errors.New("只能打开 HTTP 或 HTTPS 链接")
	}
	if s.callbacks.OpenURL == nil {
		return errors.New("浏览器尚未准备好")
	}
	return s.callbacks.OpenURL(u.String())
}
func (s *Service) OpenChat(id string) error {
	if len(id) != 36 || id[8] != '-' || id[13] != '-' || id[18] != '-' || id[23] != '-' {
		return errors.New("无法在本机定位此聊天")
	}
	for _, c := range strings.ReplaceAll(id, "-", "") {
		if !strings.ContainsRune("0123456789abcdefABCDEF", c) {
			return errors.New("无法在本机定位此聊天")
		}
	}
	if s.callbacks.OpenURL == nil {
		return errors.New("无法在本机定位此聊天")
	}
	return s.callbacks.OpenURL("codex://threads/" + id)
}
func (s *Service) SaveReportPNG(data, period string) (Row, error) {
	if len(data) > 24<<20 {
		return nil, errors.New("报告图片超过容量限制")
	}
	data = strings.TrimPrefix(data, "data:image/png;base64,")
	b, e := base64.StdEncoding.DecodeString(data)
	if e != nil || len(b) < 8 || string(b[:8]) != "\x89PNG\r\n\x1a\n" || len(b) > 16<<20 {
		return nil, errors.New("报告必须为有效 PNG 图片")
	}
	if period != "day" && period != "week" && period != "month" {
		return nil, errors.New("无效报告周期")
	}
	name := "Codexio-" + period + "-" + time.Now().Format("20060102-150405") + ".png"
	if s.callbacks.SavePNG != nil {
		p, e := s.callbacks.SavePNG(b, name)
		return Row{"path": p}, e
	}
	p := filepath.Join(s.directory, "Reports", name)
	if e = writePrivateFile(p, b); e != nil {
		return nil, e
	}
	return Row{"path": p}, nil
}
func (s *Service) GetMobile() Row { return s.mobile.Public() }
func (s *Service) MobileAction(action string, values Row) (Row, error) {
	r, e := s.mobile.Action(action, values)
	if e != nil {
		return r, e
	}
	if action == "enable" || action == "disable" {
		c, err := LoadConfig(s.directory)
		if err != nil {
			return r, err
		}
		s.mu.Lock()
		s.config = c
		s.mu.Unlock()
		s.notify("settings")
	}
	return r, nil
}
func (s *Service) UpstreamAction(enabled bool) (Row, error) {
	previous := s.upstream.Public()
	oldEnabled := ValueBool(s.Config()["upstream_detection_enabled"])
	r, e := s.upstream.Toggle(enabled)
	if e != nil {
		s.notify("upstream")
		return previous, e
	}
	if e = SaveConfig(s.directory, Row{"upstream_detection_enabled": enabled}); e != nil {
		_, _ = s.upstream.Toggle(oldEnabled)
		return previous, e
	}
	s.mu.Lock()
	s.config["upstream_detection_enabled"] = enabled
	s.mu.Unlock()
	s.quota.Refresh()
	s.notify("settings")
	return r, nil
}
func (s *Service) Ready() error {
	if s.callbacks.Action != nil {
		if e := s.callbacks.Action("ui-ready"); e != nil {
			return e
		}
	}
	if !s.mock {
		return s.updater.AcknowledgeUpdate()
	}
	return nil
}
func (s *Service) PrepareQuit() error {
	if ValueBool(s.upstream.Public()["enabled"]) {
		return s.upstream.Stop()
	}
	return nil
}
func (s *Service) Close() error {
	var result error
	s.closeOnce.Do(func() {
		s.cancel()
		_ = s.mobile.Close()
		if e := s.upstream.Stop(); e != nil {
			result = e
		}
		_ = s.quota.Close()
		_ = s.updater.Close()
		if e := s.store.Close(); e != nil && result == nil {
			result = e
		}
	})
	return result
}

func (s *Service) comparison(q Query, current Row) Row {
	days := map[string]int{"today": 1, "week": 7, "month": 30}[q.Period]
	if days == 0 || q.Start != "" || q.End != "" {
		return nil
	}
	now := time.Now()
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.Local)
	start := day.AddDate(0, 0, -days+1)
	previousQuery := q
	previousQuery.Period = "all"
	previousQuery.Start = UTCStamp(start.AddDate(0, 0, -days))
	previousQuery.End = UTCStamp(start)
	previous, e := s.store.Summary(previousQuery)
	if e != nil {
		return nil
	}
	changes := Row{}
	for _, k := range []string{"usd", "tokens", "user_requests", "cache_hit_rate"} {
		a, aok := ValueFloat(current[k])
		b, bok := ValueFloat(previous[k])
		r := Row{"percent": nil, "status": "no_history"}
		if !aok {
			r["status"] = "no_current"
		} else if bok {
			if b == 0 && a != 0 {
				r["status"] = "zero_baseline"
			} else {
				r["status"] = "ready"
				if b == 0 {
					r["percent"] = 0.0
				} else {
					r["percent"] = (a - b) / b * 100
				}
			}
		}
		changes[k] = r
		if (k == "usd" && (current["cost_complete"] == false || previous["cost_complete"] == false)) || (k == "tokens" && (metricSkipped(current, k) > 0 || metricSkipped(previous, k) > 0)) || (k == "cache_hit_rate" && (ValueInt(current["cache_skipped_records"]) > 0 || ValueInt(previous["cache_skipped_records"]) > 0)) {
			r["percent"] = nil
			r["status"] = "incomplete"
		}
	}
	label := map[string]string{"today": "较昨天", "week": "较上周", "month": "较上月"}[q.Period]
	return Row{"current": current, "previous": previous, "changes": changes, "label": label, "previous_range": start.AddDate(0, 0, -days).Format("2006.1.2") + " – " + start.Add(-time.Millisecond).Format("2006.1.2")}
}
func metricSkipped(row Row, field string) int64 {
	if skipped, ok := row["skipped"].(map[string]int); ok {
		return int64(skipped[field])
	}
	return ValueInt(ValueRow(row["skipped"])[field])
}
func (s *Service) requestAccountReports(force bool) {
	quota := s.quota.Snapshot()
	if s.mock || !ValueBool(quota["applicable"]) {
		return
	}
	account := ValueString(quota["account_key"])
	gen := s.store.Generation()
	s.reportMu.Lock()
	if s.reportBusy || (!force && s.reportAccount == account && s.reportGeneration == gen && time.Since(s.reportRead) < time.Minute) {
		s.reportMu.Unlock()
		return
	}
	s.reportBusy = true
	s.reportMu.Unlock()
	go func() {
		defer func() {
			s.reportMu.Lock()
			s.reportBusy = false
			s.reportGeneration = gen
			s.reportAccount = account
			s.reportRead = time.Now()
			s.reportMu.Unlock()
		}()
		threads := s.reportThreads()
		signature := HashString(rowJSON(threads))
		s.reportMu.Lock()
		changed := signature != s.reportSignature
		s.reportSignature = signature
		s.reportMu.Unlock()
		if changed {
			s.quota.InvalidateReports()
		}
		before := accountReportContent(ValueRow(s.quota.Snapshot()["reports"]))
		_, e := s.quota.ReadReports(threads, force)
		if e != nil {
			s.notice(e.Error())
		}
		if before != accountReportContent(ValueRow(s.quota.Snapshot()["reports"])) {
			s.notify("quota")
			s.notify("trends")
		}
	}()
}
func (s *Service) reportThreads() []Row {
	rows, e := s.store.Database().Query(`WITH local_threads AS (SELECT session_id,MIN(timestamp) AS first_meter FROM usage_priced_calls c WHERE session_id<>'' AND EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=c.id AND (s.source_id='local' OR s.source_id LIKE 'local:%')) GROUP BY session_id ORDER BY MIN(timestamp) DESC,session_id LIMIT 10000), turn_starts AS (SELECT json_extract(data,'$.session_id') AS session_id,MIN(NULLIF(json_extract(data,'$.started_at'),'')) AS started_at FROM usage_turns GROUP BY json_extract(data,'$.session_id')) SELECT l.session_id,COALESCE(t.started_at,l.first_meter) FROM local_threads l LEFT JOIN turn_starts t ON t.session_id=l.session_id ORDER BY COALESCE(t.started_at,l.first_meter) DESC,l.session_id`)
	if e != nil {
		return []Row{}
	}
	threads := map[string]Row{}
	order := []string{}
	for rows.Next() {
		var id, created string
		if rows.Scan(&id, &created) == nil {
			threads[id] = Row{"thread_id": id, "created_at": created, "descendant_thread_ids": []string{}}
			order = append(order, id)
		}
	}
	rows.Close()
	parents := map[string]string{}
	ambiguous := map[string]bool{}
	links, e := s.store.Database().Query(`SELECT DISTINCT json_extract(data,'$.session_id'),json_extract(data,'$.parent_session_id') FROM usage_turns WHERE json_extract(data,'$.parent_session_id') IS NOT NULL UNION SELECT DISTINCT json_extract(data,'$.child_session_id'),json_extract(data,'$.parent_session_id') FROM usage_agent_links WHERE json_extract(data,'$.child_session_id') IS NOT NULL`)
	if e == nil {
		for links.Next() {
			var child, parent string
			if links.Scan(&child, &parent) == nil && threads[child] != nil && threads[parent] != nil && child != parent {
				if previous := parents[child]; previous != "" && previous != parent {
					ambiguous[child] = true
				}
				parents[child] = parent
			}
		}
		links.Close()
	}
	for child := range ambiguous {
		delete(parents, child)
	}
	rootFor := func(id string) string {
		current := id
		seen := map[string]bool{}
		for parents[current] != "" {
			if seen[current] {
				return id
			}
			seen[current] = true
			current = parents[current]
		}
		return current
	}
	result := []Row{}
	for _, id := range order {
		root := rootFor(id)
		if root != id {
			threads[root]["descendant_thread_ids"] = append(ValueStrings(threads[root]["descendant_thread_ids"]), id)
		}
	}
	for _, id := range order {
		if rootFor(id) == id {
			result = append(result, threads[id])
		}
	}
	return result
}
func rowJSON(v any) string { b, _ := json.Marshal(v); return string(b) }

func (s *Service) RefreshAccountReports() Row {
	s.requestAccountReports(true)
	return s.subscription(true)
}
