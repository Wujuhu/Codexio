package backend

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	"image/jpeg"
	_ "image/png"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const mobileLocalGroup = `(EXISTS(SELECT 1 FROM json_each(json_extract(g.data,'$.source_ids')) WHERE value='local' OR value LIKE 'local:%') OR json_extract(g.data,'$.source_id')='local' OR json_extract(g.data,'$.source_id') LIKE 'local:%')`

func mobileIsLocal(r Row) bool {
	sources := ValueStrings(r["source_ids"])
	if len(sources) == 0 {
		sources = []string{ValueString(r["source_id"])}
	}
	for _, s := range sources {
		if s == "local" || strings.HasPrefix(s, "local:") {
			return true
		}
	}
	return false
}
func mobileRequest(r Row) Row {
	if len(r) == 0 {
		return nil
	}
	t, ok := ParseStamp(r["timestamp"])
	if !ok {
		return nil
	}
	status := ValueString(r["request_status"])
	if status == "" {
		status = "completed"
	}
	return Row{"id": HashString(ValueString(r["id"])), "started": float64(t.UnixMilli()) / 1000, "status": status, "preview": mobilePrefix(ValueString(r["prompt_preview"]), 80), "model": ValueString(r["model"]), "effort": r["reasoning_effort"], "speed": r["service_tier"], "tokens": r["total_tokens"], "cost": r["cost_usd"], "duration": float64(ValueInt(r["duration_ms"])) / 1000}
}
func mobileMetric(r Row) Row {
	return Row{"tokens": r["tokens"], "cost": r["usd"], "requests": ValueInt(r["user_requests"]), "costComplete": ValueBool(r["cost_complete"]), "hitRate": r["cache_hit_rate"]}
}

type mobileSum struct {
	calls    *metricSum
	requests int
}

func mobileNewSum() *mobileSum { return &mobileSum{calls: newMetricSum()} }
func (s *mobileSum) metric() Row {
	r := s.calls.row()
	r["user_requests"] = s.requests
	if s.calls.records == 0 {
		r["tokens"] = 0
		r["usd"] = 0
	}
	return mobileMetric(r)
}
func (m *MobileHost) putLocked(dataset string, value any) error {
	raw, e := mobileJSON(value)
	if e != nil {
		return e
	}
	limit := map[string]int{"live": 8000, "recent": 120000, "trends": 80000}[dataset]
	if limit == 0 || len(raw) >= limit {
		return errors.New("移动摘要超过容量限制")
	}
	digest := HashString(string(raw))
	old := m.datasets[dataset]
	if ValueString(old["digest"]) == digest {
		return nil
	}
	revision := max(ValueInt(old["revision"]), time.Now().UnixMilli()) + 1
	m.datasets[dataset] = Row{"dataset": dataset, "revision": revision, "digest": digest, "payload": string(raw)}
	return nil
}
func (m *MobileHost) project(ctx context.Context) {
	m.mu.Lock()
	if !m.enabled || m.closed {
		m.mu.Unlock()
		return
	}
	name := m.name
	before := m.datasetRevisionLocked()
	m.mu.Unlock()
	now := time.Now()
	generation := m.store.Generation()
	historyKey := fmt.Sprintf("%d:%s:%s", generation, now.Format("2006-01-02"), mobileTimeZone())
	quota := m.quota.Snapshot()
	quotaRaw, _ := mobileJSON(Row{"status": quota["status"], "updated_at": quota["updated_at"], "account": quota["account"], "primary": quota["primary"], "secondary": quota["secondary"]})
	key := historyKey + ":" + HashString(string(quotaRaw)) + ":" + name
	m.mu.Lock()
	if m.projectionKey == key {
		m.mu.Unlock()
		return
	}
	rebuild := m.historyKey != historyKey
	m.mu.Unlock()
	if rebuild {
		if e := m.projectHistory(ctx, generation, historyKey, now); e != nil {
			m.failure(e)
			return
		}
	}
	m.mu.Lock()
	if ctx.Err() != nil || !m.enabled || m.store.Generation() != generation {
		m.mu.Unlock()
		m.RequestProjection()
		return
	}
	today := CloneRow(m.todayMetric)
	task := CloneRow(m.currentTask)
	if len(task) == 0 {
		task = nil
	}
	running := 0
	if ValueString(task["status"]) == "running" {
		running = 1
	}
	sampled := now
	if t, ok := ParseStamp(quota["updated_at"]); ok {
		sampled = t
	}
	window := func(w any) Row {
		v := ValueRow(w)
		var reset, observed any
		if t, ok := ParseStamp(v["resets_at"]); ok {
			reset = float64(t.Unix())
		}
		if t, ok := ParseStamp(quota["updated_at"]); ok {
			observed = float64(t.Unix())
		}
		remaining := v["remaining_percent"]
		if len(v) == 0 {
			remaining = nil
			observed = nil
		}
		status := ValueString(quota["status"])
		return Row{"remaining": remaining, "reset": reset, "observed": observed, "retained": status == "stale" || status == "error"}
	}
	live := Row{"name": name, "timeZone": mobileTimeZone(), "observed": sampled.Unix() / 300 * 300, "task": task, "runningCount": running, "today": today, "five": window(quota["primary"]), "week": window(quota["secondary"])}
	e := m.putLocked("live", live)
	if e == nil {
		m.projectionKey = key
	}
	changed := before != m.datasetRevisionLocked()
	m.mu.Unlock()
	if e != nil {
		m.failure(e)
		return
	}
	if changed {
		m.notify()
	}
	m.upload()
}
func (m *MobileHost) datasetRevisionLocked() string {
	return fmt.Sprintf("%d:%d:%d", ValueInt(m.datasets["live"]["revision"]), ValueInt(m.datasets["recent"]["revision"]), ValueInt(m.datasets["trends"]["revision"]))
}
func (m *MobileHost) projectHistory(ctx context.Context, generation int64, key string, now time.Time) error {
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.Local)
	oldest := day.AddDate(0, 0, -89)
	days := map[string]*mobileSum{}
	dates := []time.Time{}
	for i := 0; i < 90; i++ {
		t := oldest.AddDate(0, 0, i)
		dates = append(dates, t)
		days[t.Format("2006-01-02")] = mobileNewSum()
	}
	periods := map[int]*mobileSum{}
	models := map[int]map[string]*mobileSum{}
	for _, n := range []int{7, 30, 90} {
		periods[n] = mobileNewSum()
		models[n] = map[string]*mobileSum{}
	}
	db := m.store.Database()
	rows, e := db.QueryContext(ctx, `SELECT c.data,c.timestamp FROM usage_priced_calls c WHERE timestamp>=? AND timestamp<=? AND EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=c.id AND (s.source_id='local' OR s.source_id LIKE 'local:%')) ORDER BY timestamp`, ledgerStamp(oldest), ledgerStamp(now))
	if e != nil {
		return e
	}
	for rows.Next() {
		var raw, stamp string
		if e = rows.Scan(&raw, &stamp); e != nil {
			rows.Close()
			return e
		}
		r, e := DecodeRow([]byte(raw))
		if e != nil {
			continue
		}
		t, ok := ParseStamp(stamp)
		if !ok {
			continue
		}
		t = t.In(time.Local)
		d := days[t.Format("2006-01-02")]
		if d == nil {
			continue
		}
		d.calls.add(r)
		for _, n := range []int{7, 30, 90} {
			if !t.Before(day.AddDate(0, 0, -n+1)) {
				periods[n].calls.add(r)
				name := ValueString(r["model"])
				if mobileSingleModel(name) {
					if models[n][name] == nil {
						models[n][name] = mobileNewSum()
					}
					if s := models[n][name]; s != nil {
						s.calls.add(r)
					}
				}
			}
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	rows, e = db.QueryContext(ctx, `SELECT g.data,g.timestamp FROM usage_request_groups g WHERE g.timestamp>=? AND g.timestamp<=? AND g.record_kind='user_request' AND g.is_subagent=0 AND `+mobileLocalGroup+` ORDER BY timestamp DESC,id DESC`, ledgerStamp(oldest), ledgerStamp(now))
	if e != nil {
		return e
	}
	recent := []Row{}
	ids := map[string]string{}
	preload := []string{}
	var task Row
	var latestCompleted time.Time
	for rows.Next() {
		var raw, stamp string
		if e = rows.Scan(&raw, &stamp); e != nil {
			rows.Close()
			return e
		}
		r, e := DecodeRow([]byte(raw))
		if e != nil {
			continue
		}
		t, ok := ParseStamp(stamp)
		if !ok {
			continue
		}
		t = t.In(time.Local)
		d := days[t.Format("2006-01-02")]
		if d == nil {
			continue
		}
		d.requests++
		for _, n := range []int{7, 30, 90} {
			if !t.Before(day.AddDate(0, 0, -n+1)) {
				periods[n].requests++
				name := ValueString(r["model"])
				if mobileSingleModel(name) {
					if models[n][name] == nil {
						models[n][name] = mobileNewSum()
					}
					if s := models[n][name]; s != nil {
						s.requests++
					}
				}
			}
		}
		status := ValueString(r["request_status"])
		if status == "running" && (task == nil || ValueString(task["status"]) != "running") {
			task = mobileRequest(r)
		} else if status == "completed" && (task == nil || ValueString(task["status"]) != "running") {
			ended, ok := ParseStamp(r["ended_at"])
			if !ok {
				ended = t
			}
			if ended.After(latestCompleted) {
				task = mobileRequest(r)
				latestCompleted = ended
			}
		}
		if len(recent) < 200 && !t.Before(day.AddDate(0, 0, -7)) {
			public := mobileRequest(r)
			if public != nil {
				recent = append(recent, public)
				ids[ValueString(public["id"])] = ValueString(r["id"])
				if len(preload) < 16 {
					preload = append(preload, ValueString(public["id"]))
				}
			}
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	daily := []Row{}
	for _, t := range dates {
		daily = append(daily, Row{"id": strconv.FormatInt(t.Unix(), 10) + ".0", "start": float64(t.Unix()), "metric": days[t.Format("2006-01-02")].metric()})
	}
	pp := []Row{}
	for _, n := range []int{7, 30, 90} {
		names := []string{}
		for name := range models[n] {
			names = append(names, name)
		}
		sort.Strings(names)
		mm := []Row{}
		for _, name := range names {
			mm = append(mm, Row{"id": name, "name": name, "metric": models[n][name].metric()})
		}
		pp = append(pp, Row{"days": n, "total": periods[n].metric(), "models": mm})
	}
	m.mu.Lock()
	if ctx.Err() != nil || !m.enabled || m.store.Generation() != generation {
		m.mu.Unlock()
		return nil
	}
	if e = m.putLocked("recent", recent); e == nil {
		e = m.putLocked("trends", Row{"daily": daily, "periods": pp})
	}
	if e == nil {
		m.historyKey = key
		m.requests = ids
		m.todayMetric = days[day.Format("2006-01-02")].metric()
		m.currentTask = task
	}
	m.pruneDetailsLocked()
	m.mu.Unlock()
	if e != nil {
		return e
	}
	for _, id := range preload {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		_, _ = m.getDetail(id, false)
	}
	return nil
}
func mobileSingleModel(name string) bool {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "", "unknown", "mixed", "multiple", "—", "未知", "多模型", "多个模型":
		return false
	}
	return !strings.ContainsAny(name, "+→,，\n")
}
func (m *MobileHost) pruneDetailsLocked() {
	now := float64(time.Now().Unix())
	for id, d := range m.details {
		if d.Expires <= now {
			delete(m.details, id)
			delete(m.sentDetails, id)
		}
	}
	for {
		size := 0
		for _, d := range m.details {
			size += len(ValueString(d.Envelope["payload"]))
		}
		if len(m.details) <= 64 && size <= 8<<20 {
			return
		}
		var oldest string
		var stamp time.Time
		for id, d := range m.details {
			if oldest == "" || d.Access.Before(stamp) {
				oldest = id
				stamp = d.Access
			}
		}
		delete(m.details, oldest)
		delete(m.sentDetails, oldest)
	}
}
func (m *MobileHost) getDetail(id string, thumbnails bool) (Row, error) {
	m.detailMu.Lock()
	defer m.detailMu.Unlock()
	generation := m.store.Generation()
	m.mu.Lock()
	canonical := m.requests[id]
	cached := m.details[id]
	if cached != nil && cached.Expires > float64(time.Now().Unix()) && cached.Generation == generation {
		source, _ := DecodeRow([]byte(ValueString(cached.Envelope["payload"])))
		if (!thumbnails || cached.Thumbnails) && ValueString(source["availability"]) != "partial" && ValueString(source["availability"]) != "unavailable" {
			cached.Access = time.Now()
			r := CloneRow(cached.Envelope)
			m.mu.Unlock()
			return r, nil
		}
	}
	m.mu.Unlock()
	if canonical == "" {
		return nil, errors.New("unknown mobile request")
	}
	var raw string
	if e := m.store.Database().QueryRow(`SELECT g.data FROM usage_request_groups g WHERE id=? AND record_kind='user_request' AND is_subagent=0 AND `+mobileLocalGroup, canonical).Scan(&raw); e != nil {
		return nil, e
	}
	request, e := DecodeRow([]byte(raw))
	if e != nil {
		return nil, e
	}
	start, ok := ParseStamp(request["timestamp"])
	if !ok {
		return nil, errors.New("invalid request timestamp")
	}
	ended, hasEnded := ParseStamp(request["ended_at"])
	expires := start.Add(7 * 24 * time.Hour)
	if hasEnded && ended.After(start) {
		expires = ended.Add(7 * 24 * time.Hour)
	}
	if !expires.After(time.Now()) {
		return nil, errors.New("expired request")
	}
	detail, e := m.sourceDetail(request, thumbnails)
	if e != nil {
		return nil, e
	}
	user, final := ValueString(detail["user"]), ValueString(detail["final"])
	uc, fc := ValueBool(detail["user_complete"]), ValueBool(detail["final_complete"])
	if ValueString(detail["availability"]) == "legacy_preview" {
		user = ""
		final = ""
		uc = false
		fc = false
	}
	attachments := []Row{}
	images := 0
	for _, a := range ValueRows(detail["attachments"]) {
		if len(attachments) >= 6 {
			break
		}
		name := mobilePrefix(ValueString(a["name"]), 240)
		if name == "" {
			name = "附件"
		}
		mime := mobilePrefix(ValueString(a["mime"]), 80)
		var thumb any
		if thumbnails && images < 2 && strings.HasPrefix(mime, "image/") {
			if v := mobileThumbnail(ValueString(a["path"])); v != "" {
				thumb = v
				images++
			}
		}
		attachments = append(attachments, Row{"id": HashString(ValueString(a["id"]) + name), "name": name, "mime": mime, "thumbnail": thumb})
	}
	availability := "unavailable"
	if uc && (final == "" || fc) {
		availability = "available"
	} else if user != "" || final != "" {
		availability = "partial"
	}
	var completed any
	if hasEnded {
		completed = float64(ended.UnixMilli()) / 1000
	}
	value := Row{"id": id, "started": float64(start.UnixMilli()) / 1000, "completed": completed, "status": ValueString(request["request_status"]), "user": user, "final": final, "userComplete": uc, "finalComplete": fc, "availability": availability, "attachments": attachments, "full": true}
	payload, e := mobileJSON(value)
	if e != nil {
		return nil, e
	}
	if len(payload) > mobileDetailLimit {
		value["user"] = mobilePrefix(user, 1200)
		value["final"] = mobilePrefix(final, 5000)
		value["userComplete"] = false
		value["finalComplete"] = false
		value["availability"] = "capacity"
		for _, a := range attachments {
			a["thumbnail"] = nil
		}
		payload, e = mobileJSON(value)
		if e != nil || len(payload) > mobileDetailLimit {
			return nil, errors.New("detail exceeds capacity")
		}
	}
	digest := HashString(string(payload))
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.enabled || m.store.Generation() != generation || m.requests[id] != canonical {
		return nil, errors.New("request changed")
	}
	old := m.details[id]
	revision := time.Now().UnixMilli() + 1
	if old != nil {
		if ValueString(old.Envelope["digest"]) == digest {
			revision = ValueInt(old.Envelope["revision"])
		} else {
			revision = max(revision, ValueInt(old.Envelope["revision"])+1)
		}
	}
	envelope := Row{"dataset": "detail-" + id, "revision": revision, "digest": digest, "payload": string(payload)}
	m.details[id] = &mobileDetail{Envelope: envelope, Expires: float64(expires.Unix()), Access: time.Now(), Generation: generation, Thumbnails: thumbnails}
	m.pruneDetailsLocked()
	go m.upload()
	return CloneRow(envelope), nil
}

// Thumbnail reads only an indexed attachment supplied by the verified request;
// dimensions are inspected before decoding and all exported bytes are bounded.
func mobileThumbnail(path string) string {
	if path == "" || !filepath.IsAbs(path) {
		return ""
	}
	st, e := os.Lstat(path)
	if e != nil || !st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0 || st.Size() > 12<<20 {
		return ""
	}
	f, e := os.Open(path)
	if e != nil {
		return ""
	}
	defer f.Close()
	cfg, _, e := image.DecodeConfig(io.LimitReader(f, 12<<20))
	if e != nil || cfg.Width < 1 || cfg.Height < 1 || cfg.Width > 16384 || cfg.Height > 16384 || int64(cfg.Width)*int64(cfg.Height) > 40000000 {
		return ""
	}
	if _, e = f.Seek(0, 0); e != nil {
		return ""
	}
	src, _, e := image.Decode(io.LimitReader(f, 12<<20))
	if e != nil {
		return ""
	}
	w, h := cfg.Width, cfg.Height
	if max(w, h) > 240 {
		w = max(1, w*240/max(cfg.Width, cfg.Height))
		h = max(1, h*240/max(cfg.Width, cfg.Height))
	}
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	b := src.Bounds()
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			dst.Set(x, y, src.At(b.Min.X+x*cfg.Width/w, b.Min.Y+y*cfg.Height/h))
		}
	}
	var out bytes.Buffer
	if jpeg.Encode(&out, dst, &jpeg.Options{Quality: 55}) != nil || out.Len() > 12288 {
		return ""
	}
	return base64.StdEncoding.EncodeToString(out.Bytes())
}
