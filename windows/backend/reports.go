package backend

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"
)

type ReportManager struct {
	mu        sync.Mutex
	store     *Store
	directory string
	config    func() Row
	key       string
	documents map[string]Row
	seen      map[string]bool
}

func NewReportManager(store *Store, dir string, config func() Row) *ReportManager {
	r := &ReportManager{store: store, directory: filepath.Join(dir, "Reports"), config: config, documents: map[string]Row{}, seen: map[string]bool{}}
	if v, e := ReadJSON(filepath.Join(r.directory, "presentation.json")); e == nil {
		for _, d := range ValueStrings(v["days"]) {
			r.seen[d] = true
		}
	}
	return r
}
func PreferredReportPeriod(t time.Time) string {
	if t.Day() == 1 {
		return "month"
	}
	if t.Weekday() == time.Monday {
		return "week"
	}
	return "day"
}
func (r *ReportManager) Due() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	now := time.Now()
	return ValueBool(r.config()["usage_report_auto"]) && now.Hour() >= 8 && !r.seen[now.Format("2006-01-02")]
}
func (r *ReportManager) MarkSeen() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.seen[time.Now().Format("2006-01-02")] = true
	days := []string{}
	for d := range r.seen {
		days = append(days, d)
	}
	sort.Strings(days)
	if len(days) > 90 {
		days = days[len(days)-90:]
	}
	r.seen = map[string]bool{}
	for _, d := range days {
		r.seen[d] = true
	}
	return WriteJSON(filepath.Join(r.directory, "presentation.json"), Row{"days": days})
}
func (r *ReportManager) Get(period string) (Row, error) {
	if period != "day" && period != "week" && period != "month" {
		return nil, errors.New("无效报告周期")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	now := time.Now()
	_, offset := now.Zone()
	cfg := r.config()
	key := rowJSON([]any{r.store.Generation(), now.Format("2006-01-02"), offset, ValueString(cfg["language"])})
	if key != r.key {
		r.documents = map[string]Row{}
		r.key = key
	}
	if d := r.documents[period]; d != nil {
		out := CloneRow(d)
		out["style"] = cfg["usage_report_style"]
		return out, nil
	}
	start, end, prior := reportBounds(period, now)
	q := Query{Period: "all", Start: UTCStamp(start), End: UTCStamp(end), Source: "local"}
	summary, e := r.store.Summary(q)
	if e != nil {
		return nil, e
	}
	summary = closedReportSummary(summary)
	models, e := r.store.Models(q)
	if e != nil {
		return nil, e
	}
	previous, e := r.store.Summary(Query{Period: "all", Start: UTCStamp(prior), End: UTCStamp(start), Source: "local"})
	if e != nil {
		return nil, e
	}
	previous = closedReportSummary(previous)
	sort.SliceStable(models, func(i, j int) bool {
		a := ValueInt(models[i]["call_count"])
		if a == 0 {
			a = ValueInt(models[i]["calls"])
		}
		b := ValueInt(models[j]["call_count"])
		if b == 0 {
			b = ValueInt(models[j]["calls"])
		}
		return a > b
	})
	if len(models) > 3 {
		models = models[:3]
	}
	slices := []Row{{"name": "凌晨", "requests": 0}, {"name": "上午", "requests": 0}, {"name": "午后", "requests": 0}, {"name": "晚间", "requests": 0}}
	var first, last any
	firstClock, lastClock := 86400, -1
	rows, e := r.store.Database().Query(`SELECT timestamp,data FROM usage_request_groups WHERE timestamp>=? AND timestamp<? AND record_kind='user_request' AND is_subagent=0 ORDER BY timestamp`, reportIndexStamp(start), reportIndexStamp(end))
	if e != nil {
		return nil, e
	}
	for rows.Next() {
		var ts, raw string
		if rows.Scan(&ts, &raw) != nil {
			continue
		}
		v, e := DecodeRow([]byte(raw))
		if e != nil || !reportLocal(v) {
			continue
		}
		t, ok := ParseStamp(ts)
		if !ok {
			continue
		}
		t = t.In(time.Local)
		idx := min(3, t.Hour()/6)
		slices[idx]["requests"] = ValueInt(slices[idx]["requests"]) + 1
		clock := t.Hour()*3600 + t.Minute()*60 + t.Second()
		if clock < firstClock {
			firstClock = clock
			first = UTCStamp(t)
		}
		if clock > lastClock {
			lastClock = clock
			last = UTCStamp(t)
		}
	}
	rows.Close()
	label := start.Format("2006.01.02")
	if period == "week" {
		label += " — " + end.AddDate(0, 0, -1).Format("01.02")
	}
	if period == "month" {
		label = start.Format("2006.01")
	}
	d := Row{"period": period, "start": start.Format("2006-01-02"), "end": end.Format("2006-01-02"), "date_label": label, "summary": summary, "models": models, "time_slices": slices, "first": first, "last": last, "previous": previous, "style": cfg["usage_report_style"], "source_id": HashString(r.store.path)[:16]}
	p := filepath.Join(r.directory, HashString(r.store.path)[:16], period, start.Format("2006-01-02")+".json")
	b, _ := json.MarshalIndent(d, "", "  ")
	old, _ := os.ReadFile(p)
	if string(old) != string(b) {
		if e = writePrivateFile(p, b); e != nil {
			return nil, e
		}
	}
	r.documents[period] = d
	return CloneRow(d), nil
}
func closedReportSummary(summary Row) Row {
	if ValueInt(summary["records"]) == 0 {
		for _, key := range []string{"tokens", "usd", "requests", "input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"} {
			summary[key] = 0
		}
		summary["cost_complete"] = true
	}
	return summary
}
func reportBounds(period string, t time.Time) (time.Time, time.Time, time.Time) {
	day := time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.Local)
	switch period {
	case "week":
		shift := (int(day.Weekday()) + 6) % 7
		end := day.AddDate(0, 0, -shift)
		return end.AddDate(0, 0, -7), end, end.AddDate(0, 0, -14)
	case "month":
		end := time.Date(day.Year(), day.Month(), 1, 0, 0, 0, 0, time.Local)
		start := end.AddDate(0, -1, 0)
		return start, end, start.AddDate(0, -1, 0)
	default:
		return day.AddDate(0, 0, -1), day, day.AddDate(0, 0, -2)
	}
}
func reportIndexStamp(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05.000Z") }
func reportLocal(row Row) bool {
	sources := ValueStrings(row["source_ids"])
	if len(sources) == 0 {
		sources = []string{ValueString(row["source_id"])}
	}
	for _, s := range sources {
		if s == "local" || len(s) > 6 && s[:6] == "local:" {
			return true
		}
	}
	return false
}
