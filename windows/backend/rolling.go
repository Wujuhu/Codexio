package backend

// Account-scoped, mature observations ported from rolling_estimation.py.
// Samples are supplied by the existing quota reader; this adds no collector.
import (
	"encoding/json"
	"math"
	"sync"
	"time"
)

type rollingWindow struct {
	start, end Row
	minutes    int64
}

type RollingEstimator struct {
	mu             sync.Mutex
	store          *Store
	config         func() Row
	anchor, latest Row
	completed      []rollingWindow
	acceptAfter    float64
	lastCheck      time.Time
	lastAwake      float64
	interval       int64
	priceVersion   string
	changed        func()
	limitID        string
}

func NewRollingEstimator(store *Store, config func() Row, changed func(), limits ...string) (*RollingEstimator, error) {
	_, err := store.db.Exec(`CREATE TABLE IF NOT EXISTS usage_week_intervals(id INTEGER PRIMARY KEY AUTOINCREMENT,end_at TEXT NOT NULL,data TEXT NOT NULL);
	CREATE INDEX IF NOT EXISTS week_intervals_end ON usage_week_intervals(end_at DESC,id DESC)`)
	if err != nil {
		return nil, err
	}
	limit := "codex"
	if len(limits) > 0 {
		limit = limits[0]
	}
	return &RollingEstimator{store: store, config: config, changed: changed, limitID: limit, acceptAfter: float64(time.Now().UnixNano()) / 1e9}, nil
}

const rollingBoundaryTolerance = 5.0

func (e *RollingEstimator) invalidate() {
	e.anchor = nil
	e.latest = nil
	e.acceptAfter = float64(time.Now().UnixNano()) / 1e9
}
func (e *RollingEstimator) Sample(quota Row) {
	e.mu.Lock()
	defer e.mu.Unlock()
	interval := ValueInt(e.config()["week_estimate_interval_minutes"])
	if interval != 10 && interval != 30 && interval != 60 {
		interval = 10
	}
	if e.interval != interval {
		e.interval = interval
		e.anchor = nil
		e.latest = nil
	}
	stamp, ok := ParseStamp(quota["updated_at"])
	at := float64(stamp.UnixNano()) / 1e9
	w := ValueRow(quota["secondary"])
	used, uok := ValueFloat(w["used_percent"])
	reset, rok := ParseStamp(w["resets_at"])
	account, plan, limit := ValueString(quota["account_key"]), ValueString(quota["plan_type"]), ValueString(quota["limit_id"])
	if e.limitID == "claude" && plan == "" {
		plan = "claude"
	}
	if dataString(quota, "status") != "ok" || !ok || at < e.acceptAfter || at > float64(time.Now().UnixNano())/1e9+rollingBoundaryTolerance || !uok || used < 0 || used > 100 || !rok || !reset.After(stamp) || account == "" || plan == "" || plan == "unknown" || plan == "legacy" || limit != e.limitID {
		e.invalidate()
		return
	}
	buckets := ValueRow(quota["rate_limits_by_limit_id"])
	gap := float64(ValueInt(e.config()["refresh_interval_seconds"])) + 30
	if e.limitID == "claude" {
		gap = 90
	}
	gap = math.Max(45, math.Min(330, gap))
	boundary := true
	if v, exists := quota["boundary_sample"]; exists {
		boundary = ValueBool(v)
	}
	sample := Row{"timestamp": at, "used_percent": used, "reset_at": float64(reset.UnixNano()) / 1e9, "account_key": account, "plan_type": plan, "limit_id": limit, "sole_codex_pool": len(buckets) == 1 && len(ValueRow(buckets["codex"])) > 0, "max_gap_seconds": gap}
	if len(e.latest) > 0 {
		previous, _ := ValueFloat(e.latest["timestamp"])
		if at <= previous {
			return
		}
		prevReset, _ := ValueFloat(e.latest["reset_at"])
		prevUsed, _ := ValueFloat(e.latest["used_percent"])
		if account != dataString(e.latest, "account_key") || plan != dataString(e.latest, "plan_type") || sample["sole_codex_pool"] != e.latest["sole_codex_pool"] || math.Abs(float64(reset.UnixNano())/1e9-prevReset) > 1 || used < prevUsed || at-previous > gap {
			e.anchor = nil
		}
	}
	e.latest = sample
	if e.anchor == nil {
		if boundary {
			e.anchor = sample
		}
		return
	}
	start, _ := ValueFloat(e.anchor["timestamp"])
	due := start + float64(interval*60)
	if at > due+rollingBoundaryTolerance {
		e.anchor = nil
		if boundary {
			e.anchor = sample
		}
		return
	}
	if boundary && math.Abs(at-due) <= rollingBoundaryTolerance {
		e.completed = append(e.completed, rollingWindow{e.anchor, sample, interval})
		if len(e.completed) > 12 {
			e.completed = e.completed[len(e.completed)-12:]
		}
		e.anchor = sample
	}
}

func (e *RollingEstimator) Interrupt() {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.invalidate()
	e.completed = nil
}
func (e *RollingEstimator) Process() { e.mu.Lock(); defer e.mu.Unlock(); e.process(time.Now()) }
func (e *RollingEstimator) process(now time.Time) {
	awake := systemAwakeSeconds()
	if !e.lastCheck.IsZero() {
		wall := float64(now.UnixNano()-e.lastCheck.UnixNano()) / 1e9
		elapsed := now.Sub(e.lastCheck).Seconds()
		if wall < 0 || wall > 90 || math.Abs(wall-elapsed) > 5 || (awake > 0 && e.lastAwake > 0 && math.Abs(wall-(awake-e.lastAwake)) > 5) {
			e.invalidate()
			e.completed = nil
		}
	}
	e.lastCheck = now
	e.lastAwake = awake
	if len(e.latest) > 0 {
		at, _ := ValueFloat(e.latest["timestamp"])
		gap, _ := ValueFloat(e.latest["max_gap_seconds"])
		if float64(now.UnixNano())/1e9-at > gap {
			e.invalidate()
		}
	}
	version := ValueString(e.store.Status()["price_version"])
	if version != e.priceVersion {
		if e.reprice(version) != nil {
			return
		}
		e.priceVersion = version
	}
	for len(e.completed) > 0 {
		window := e.completed[0]
		end, _ := ValueFloat(window.end["timestamp"])
		if end > float64(now.UnixNano())/1e9-120 {
			break
		}
		e.completed = e.completed[1:]
		start, _ := ValueFloat(window.start["timestamp"])
		before, _ := ValueFloat(window.start["used_percent"])
		after, _ := ValueFloat(window.end["used_percent"])
		delta := after - before
		if delta <= 0 || math.Abs(end-start-float64(window.minutes*60)) > rollingBoundaryTolerance {
			continue
		}
		startAt, _ := ParseStamp(start)
		endAt, _ := ParseStamp(end)
		tokens, dollars, invalid, err := e.usage(startAt, endAt, ValueBool(window.start["sole_codex_pool"]))
		if err != nil || invalid || dollars <= 0 {
			continue
		}
		r := Row{"start": UTCStamp(startAt), "end": UTCStamp(endAt), "account_key": window.end["account_key"], "plan_type": window.end["plan_type"], "limit_id": e.limitID, "reset_at": window.end["reset_at"], "sole_codex_pool": window.start["sole_codex_pool"], "start_percent": before, "end_percent": after, "delta_percent": delta, "consumed_tokens": tokens, "consumed_usd": dollars, "estimated_total_usd": 100 * dollars / delta, "price_version": version, "interval_minutes": window.minutes, "duration_seconds": end - start, "fixed_window_version": 1}
		if _, err = e.store.db.Exec("INSERT INTO usage_week_intervals(end_at,data) VALUES(?,?)", UTCStamp(endAt), dataJSON(r)); err == nil && e.changed != nil {
			e.changed()
		}
	}
}

func (e *RollingEstimator) usage(start, end time.Time, sole bool) (int64, float64, bool, error) {
	if e.limitID == "claude" {
		rows, err := e.store.db.Query(`SELECT data FROM usage_priced_calls WHERE timestamp>? AND timestamp<=? AND json_extract(data,'$.client_kind')='claude_code' AND coalesce(json_extract(data,'$.service_tier'),'') NOT IN ('priority','fast') AND coalesce(json_extract(data,'$.auth_mode'),'') NOT IN ('api','api_key','apikey') AND coalesce(json_extract(data,'$.billing_source'),'') NOT IN ('credits','api','api_key','usage_credits')`, ledgerStamp(start), ledgerStamp(end))
		if err != nil {
			return 0, 0, true, err
		}
		defer rows.Close()
		var tokens int64
		var cost float64
		invalid := false
		for rows.Next() {
			var raw string
			if err = rows.Scan(&raw); err != nil {
				return 0, 0, true, err
			}
			r := dataRow(raw)
			n, known := knownCount(r["total_tokens"])
			amount, priced := ValueFloat(r["cost_usd"])
			if !known || !priced {
				invalid = true
				continue
			}
			tokens += n
			cost += amount
		}
		return tokens, cost, invalid, rows.Err()
	}

	rows, err := e.store.db.Query(`SELECT data FROM usage_priced_calls c WHERE timestamp>? AND timestamp<=? AND coalesce(json_extract(c.data,'$.client_kind'),'codex')='codex' AND EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=c.id AND (s.source_id='local' OR s.source_id LIKE 'local:%')) ORDER BY timestamp,id`, ledgerStamp(start), ledgerStamp(end))
	if err != nil {
		return 0, 0, true, err
	}
	defer rows.Close()
	var tokens int64
	var dollars, correction float64
	invalid := false
	for rows.Next() {
		var raw string
		if err = rows.Scan(&raw); err != nil {
			return 0, 0, true, err
		}
		r, err := DecodeRow([]byte(raw))
		if err != nil {
			invalid = true
			continue
		}
		provider := ValueString(r["provider"])
		if provider != "openai" && provider != "codexio-upstream" {
			continue
		}
		bucket := ValueString(r["limit_id"])
		if bucket != "codex" && !(sole && bucket == "") {
			invalid = true
			continue
		}
		cost, cok := ValueFloat(r["cost_usd"])
		count, tok := ValueFloat(r["total_tokens"])
		status := ValueString(r["pricing_status"])
		if !cok || cost < 0 || !tok || count < 0 || count != math.Trunc(count) || (status != "priced" && status != "estimated") {
			invalid = true
			continue
		}
		tokens += int64(count)
		adjusted := cost - correction
		next := dollars + adjusted
		correction = (next - dollars) - adjusted
		dollars = next
	}
	return tokens, dollars, invalid, rows.Err()
}

func (e *RollingEstimator) reprice(version string) error {
	rows, err := e.store.db.Query("SELECT id,data FROM usage_week_intervals WHERE coalesce(json_extract(data,'$.limit_id'),'codex')=? ORDER BY end_at DESC,id DESC", e.limitID)
	if err != nil {
		return err
	}
	type stored struct {
		id int64
		r  Row
	}
	pending := []stored{}
	for rows.Next() {
		var id int64
		var raw string
		if err = rows.Scan(&id, &raw); err != nil {
			rows.Close()
			return err
		}
		r, _ := DecodeRow([]byte(raw))
		if ValueString(r["price_version"]) != version {
			pending = append(pending, stored{id, r})
		}
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	for _, v := range pending {
		start, sok := ParseStamp(v.r["start"])
		end, eok := ParseStamp(v.r["end"])
		delta, dok := ValueFloat(v.r["delta_percent"])
		tokens, cost, invalid, err := e.usage(start, end, ValueBool(v.r["sole_codex_pool"]))
		if err != nil {
			return err
		}
		if !sok || !eok || !dok || delta <= 0 || invalid || cost <= 0 {
			if _, err = e.store.db.Exec("DELETE FROM usage_week_intervals WHERE id=?", v.id); err != nil {
				return err
			}
			continue
		}
		v.r["consumed_tokens"] = tokens
		v.r["consumed_usd"] = cost
		v.r["estimated_total_usd"] = 100 * cost / delta
		v.r["price_version"] = version
		b, _ := json.Marshal(v.r)
		if _, err = e.store.db.Exec("UPDATE usage_week_intervals SET data=? WHERE id=?", string(b), v.id); err != nil {
			return err
		}
	}
	if len(pending) > 0 && e.changed != nil {
		e.changed()
	}
	return nil
}

func (e *RollingEstimator) Rows(account string) []Row {
	if account == "" {
		return []Row{}
	}
	rows, err := e.store.db.Query("SELECT data FROM usage_week_intervals WHERE json_extract(data,'$.account_key')=? AND coalesce(json_extract(data,'$.limit_id'),'codex')=? ORDER BY end_at DESC,id DESC LIMIT 20", account, e.limitID)
	if err != nil {
		return []Row{}
	}
	defer rows.Close()
	result := []Row{}
	for rows.Next() {
		var raw string
		if rows.Scan(&raw) == nil {
			if r, err := DecodeRow([]byte(raw)); err == nil {
				result = append(result, r)
			}
		}
	}
	return result
}
