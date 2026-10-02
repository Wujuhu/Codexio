package backend

// Account-scoped, mature observations ported from rolling_estimation.py.
// Samples are supplied by the existing quota reader; this adds no collector.
import (
	"encoding/json"
	"math"
	"sync"
	"time"
)

type RollingEstimator struct {
	mu                         sync.Mutex
	store                      *Store
	config                     func() Row
	anchor, checkpoint, latest Row
	pending                    []Row
	interval                   int64
	priceVersion               string
	changed                    func()
}

func NewRollingEstimator(store *Store, config func() Row, changed func()) (*RollingEstimator, error) {
	_, err := store.db.Exec(`CREATE TABLE IF NOT EXISTS usage_week_intervals(id INTEGER PRIMARY KEY AUTOINCREMENT,end_at TEXT NOT NULL,data TEXT NOT NULL);
	CREATE INDEX IF NOT EXISTS week_intervals_end ON usage_week_intervals(end_at DESC,id DESC)`)
	if err != nil {
		return nil, err
	}
	return &RollingEstimator{store: store, config: config, changed: changed}, nil
}

func (e *RollingEstimator) invalidate() {
	e.anchor = nil
	e.checkpoint = nil
	e.latest = nil
	e.pending = nil
}
func (e *RollingEstimator) Sample(quota Row) {
	e.mu.Lock()
	defer e.mu.Unlock()
	interval := ValueInt(e.config()["week_estimate_interval_minutes"])
	if interval != 10 && interval != 30 && interval != 60 {
		interval = 30
	}
	if e.interval != interval {
		e.interval = interval
		e.invalidate()
	}
	stamp, ok := ParseStamp(quota["updated_at"])
	w := ValueRow(quota["secondary"])
	used, uok := ValueFloat(w["used_percent"])
	reset, rok := ParseStamp(w["resets_at"])
	account, plan, limit := ValueString(quota["account_key"]), ValueString(quota["plan_type"]), ValueString(quota["limit_id"])
	if ValueString(quota["status"]) != "ok" || !ok || !uok || used < 0 || used > 100 || !rok || !reset.After(stamp) || account == "" || plan == "" || plan == "unknown" || plan == "legacy" || limit != "codex" {
		e.invalidate()
		return
	}
	buckets := ValueRow(quota["rate_limits_by_limit_id"])
	sample := Row{"timestamp": float64(stamp.UnixNano()) / 1e9, "used_percent": used, "reset_at": float64(reset.UnixNano()) / 1e9, "account_key": account, "plan_type": plan, "limit_id": limit, "sole_codex_pool": len(buckets) == 1 && len(ValueRow(buckets["codex"])) > 0}
	if len(e.latest) > 0 {
		now, _ := ValueFloat(sample["timestamp"])
		previous, _ := ValueFloat(e.latest["timestamp"])
		prevReset, _ := ValueFloat(e.latest["reset_at"])
		if sample["account_key"] != e.latest["account_key"] || sample["plan_type"] != e.latest["plan_type"] || sample["sole_codex_pool"] != e.latest["sole_codex_pool"] || math.Abs(float64(reset.UnixNano())/1e9-prevReset) > 60 || now <= previous || now-previous > 600 {
			e.invalidate()
		}
	}
	e.latest = sample
	if e.anchor == nil {
		e.anchor = sample
		e.checkpoint = sample
	} else {
		e.pending = append(e.pending, sample)
		if len(e.pending) > 180 {
			e.pending = e.pending[len(e.pending)-180:]
		}
	}
	e.process(time.Now())
}

func (e *RollingEstimator) Process() { e.mu.Lock(); defer e.mu.Unlock(); e.process(time.Now()) }
func (e *RollingEstimator) process(now time.Time) {
	version := ValueString(e.store.Status()["price_version"])
	if version != e.priceVersion {
		if e.reprice(version) != nil {
			return
		}
		e.priceVersion = version
	}
	if e.checkpoint == nil {
		return
	}
	checkpoint, _ := ValueFloat(e.checkpoint["timestamp"])
	mature := float64(now.UnixNano())/1e9 - 120
	index := -1
	for i, s := range e.pending {
		at, _ := ValueFloat(s["timestamp"])
		if at <= mature && at-checkpoint >= float64(e.interval*60) {
			index = i
		}
	}
	if index < 0 {
		return
	}
	sample := e.pending[index]
	e.pending = e.pending[index+1:]
	e.checkpoint = sample
	start := e.anchor
	endPercent, _ := ValueFloat(sample["used_percent"])
	startPercent, _ := ValueFloat(start["used_percent"])
	delta := endPercent - startPercent
	if delta < 0 {
		e.anchor = sample
		return
	}
	if delta == 0 {
		return
	}
	startAt, _ := ParseStamp(start["timestamp"])
	endAt, _ := ParseStamp(sample["timestamp"])
	tokens, dollars, invalid, err := e.usage(startAt, endAt, ValueBool(start["sole_codex_pool"]))
	e.anchor = sample
	if err != nil || invalid || dollars <= 0 {
		return
	}
	r := Row{"start": UTCStamp(startAt), "end": UTCStamp(endAt), "account_key": sample["account_key"], "plan_type": sample["plan_type"], "limit_id": "codex", "reset_at": sample["reset_at"], "sole_codex_pool": start["sole_codex_pool"], "start_percent": startPercent, "end_percent": endPercent, "delta_percent": delta, "consumed_tokens": tokens, "consumed_usd": dollars, "estimated_total_usd": 100 * dollars / delta, "price_version": version}
	b, _ := json.Marshal(r)
	tx, err := e.store.db.Begin()
	if err != nil {
		return
	}
	defer tx.Rollback()
	if _, err = tx.Exec("INSERT INTO usage_week_intervals(end_at,data) VALUES(?,?)", ledgerStamp(endAt), string(b)); err != nil {
		return
	}
	if tx.Commit() == nil && e.changed != nil {
		e.changed()
	}
}

func (e *RollingEstimator) usage(start, end time.Time, sole bool) (int64, float64, bool, error) {
	rows, err := e.store.db.Query(`SELECT data FROM usage_priced_calls c WHERE timestamp>? AND timestamp<=? AND EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=c.id AND (s.source_id='local' OR s.source_id LIKE 'local:%')) ORDER BY timestamp,id`, ledgerStamp(start), ledgerStamp(end))
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
	rows, err := e.store.db.Query("SELECT id,data FROM usage_week_intervals ORDER BY end_at DESC,id DESC")
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
	rows, err := e.store.db.Query("SELECT data FROM usage_week_intervals WHERE json_extract(data,'$.account_key')=? ORDER BY end_at DESC,id DESC LIMIT 20", account)
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
