package backend

import (
	"database/sql"
	"fmt"
	"strings"
	"time"
)

func projectionCalendar(now time.Time) string {
	zone, offset := now.Zone()
	return fmt.Sprint(now.Location(), "|", now.Format("2006-01-02"), "|", zone, "|", offset)
}

// Only metrics and counted user requests affect the common chart. A change
// to a title, preview or duration alone does not invalidate a date.
func beginBucketChanges(tx *sql.Tx) error {
	if _, err := tx.Exec(`CREATE TEMP TABLE IF NOT EXISTS go_bucket_capture(active INTEGER PRIMARY KEY);
		CREATE TEMP TABLE IF NOT EXISTS go_bucket_changes(timestamp TEXT PRIMARY KEY) WITHOUT ROWID;
		INSERT INTO go_bucket_capture VALUES(1);`); err != nil {
		return err
	}
	for _, table := range []struct {
		name, relevant, change string
	}{
		{"usage_priced_calls", "1", "OLD.metrics IS NOT NEW.metrics"},
		{"usage_request_groups", "{row}.record_kind='user_request' AND {row}.is_subagent=0",
			"OLD.record_kind IS NOT NEW.record_kind OR OLD.is_subagent IS NOT NEW.is_subagent"},
	} {
		for _, op := range []struct {
			name string
			rows []string
		}{{"INSERT", []string{"NEW"}}, {"UPDATE", []string{"OLD", "NEW"}}, {"DELETE", []string{"OLD"}}} {
			when := "EXISTS(SELECT 1 FROM go_bucket_capture)"
			if op.name == "UPDATE" {
				when += " AND (OLD.timestamp IS NOT NEW.timestamp OR " + table.change +
					" OR json_extract(OLD.data,'$.client_kind') IS NOT json_extract(NEW.data,'$.client_kind')" +
					" OR json_extract(OLD.data,'$.source_ids') IS NOT json_extract(NEW.data,'$.source_ids'))"
			}
			var body strings.Builder
			for _, row := range op.rows {
				body.WriteString("INSERT INTO go_bucket_changes SELECT " + row + ".timestamp WHERE " +
					strings.ReplaceAll(table.relevant, "{row}", row) + " ON CONFLICT(timestamp) DO NOTHING;")
			}
			statement := "CREATE TEMP TRIGGER IF NOT EXISTS go_bucket_" + table.name + "_" + op.name +
				" AFTER " + op.name + " ON main." + table.name + " WHEN " + when + " BEGIN " + body.String() + " END"
			if _, err := tx.Exec(statement); err != nil {
				return err
			}
		}
	}
	return nil
}

func rebuildUsageBuckets(tx *sql.Tx, scope *projectionScope, location *time.Location) error {
	if scope != nil {
		rows, err := tx.Query("SELECT timestamp FROM go_bucket_changes")
		if err != nil {
			return err
		}
		days := map[time.Time]bool{}
		for rows.Next() {
			var raw string
			if err = rows.Scan(&raw); err != nil {
				rows.Close()
				return err
			}
			if t, ok := ParseStamp(raw); ok {
				days[projectionDay(t, location)] = true
			}
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		for day := range days {
			if err = rebuildUsageDay(tx, day); err != nil {
				return err
			}
		}
		_, err = tx.Exec("DELETE FROM go_bucket_changes; DELETE FROM go_bucket_capture;")
		return err
	}
	if _, err := tx.Exec("DELETE FROM usage_query_day_buckets"); err != nil {
		return err
	}
	// Seek to the next populated day via the timestamp indexes. Full recovery
	// only retains one day's aggregate in memory, even after a large import.
	cursor := " "
	for {
		var next sql.NullString
		if err := tx.QueryRow(`SELECT min(stamp) FROM (
			SELECT (SELECT min(timestamp) FROM usage_priced_calls WHERE timestamp>=?) stamp
			UNION ALL SELECT (SELECT min(timestamp) FROM usage_request_groups WHERE timestamp>=? AND record_kind='user_request' AND is_subagent=0))`, cursor, cursor).Scan(&next); err != nil {
			return err
		}
		if !next.Valid {
			return nil
		}
		t, ok := ParseStamp(next.String)
		if !ok {
			cursor = next.String + "\x01"
			continue
		}
		day := projectionDay(t, location)
		upper := ledgerStamp(day.AddDate(0, 0, 1))
		if upper <= next.String {
			// Legacy noncanonical times cannot seed a UTC index seek.
			cursor = next.String + "\x01"
			continue
		}
		if err := rebuildUsageDay(tx, day); err != nil {
			return err
		}
		cursor = upper
	}
}

func projectionDay(t time.Time, location *time.Location) time.Time {
	t = t.In(location)
	return time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, location)
}

type usageBucket struct {
	metrics  *metricSum
	requests int
}

func newUsageBucket() *usageBucket { return &usageBucket{metrics: newMetricSum()} }

func (b *usageBucket) row() Row {
	row := b.metrics.summaryRow()
	row["user_requests"] = b.requests
	return row
}

type usageDayBucket struct {
	first, last string
	total       *usageBucket
	hours       map[string]*usageBucket
}

func rebuildUsageDay(tx *sql.Tx, day time.Time) error {
	key := day.Format("2006-01-02")
	start, end := ledgerStamp(day), ledgerStamp(day.AddDate(0, 0, 1))
	type scopeKey struct {
		client string
		local  bool
	}
	buckets := map[scopeKey]*usageDayBucket{}
	add := func(stamp, client string, local bool, metrics Row) {
		t, ok := ParseStamp(stamp)
		if !ok {
			return
		}
		hour := chartKey(t, "hour")
		for _, localOnly := range []bool{false, true} {
			if localOnly && !local {
				continue
			}
			scope := scopeKey{client, localOnly}
			b := buckets[scope]
			if b == nil {
				b = &usageDayBucket{first: stamp, last: stamp, total: newUsageBucket(), hours: map[string]*usageBucket{}}
				buckets[scope] = b
			}
			b.first, b.last = min(b.first, stamp), max(b.last, stamp)
			if b.hours[hour] == nil {
				b.hours[hour] = newUsageBucket()
			}
			for _, cell := range []*usageBucket{b.total, b.hours[hour]} {
				if metrics == nil {
					cell.requests++
				} else {
					cell.metrics.merge(metrics, client == "claude_code")
				}
			}
		}
	}
	rows, err := tx.Query(`SELECT p.timestamp,`+rankClient+`,`+rankLocalCalls+`,p.metrics
		FROM usage_priced_calls p WHERE p.timestamp>=? AND p.timestamp<? ORDER BY p.timestamp,p.id`, start, end)
	if err != nil {
		return err
	}
	for rows.Next() {
		var stamp, client, raw string
		var local bool
		if err = rows.Scan(&stamp, &client, &local, &raw); err != nil {
			rows.Close()
			return err
		}
		metrics, decodeErr := DecodeRow([]byte(raw))
		if decodeErr != nil {
			rows.Close()
			return decodeErr
		}
		add(stamp, client, local, metrics)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	rows, err = tx.Query(`SELECT g.timestamp,coalesce(json_extract(g.data,'$.client_kind'),'codex'),`+rankLocalRequests+`
		FROM usage_request_groups g WHERE g.timestamp>=? AND g.timestamp<? AND g.record_kind='user_request' AND g.is_subagent=0`, start, end)
	if err != nil {
		return err
	}
	for rows.Next() {
		var stamp, client string
		var local bool
		if err = rows.Scan(&stamp, &client, &local); err != nil {
			rows.Close()
			return err
		}
		add(stamp, client, local, nil)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	if _, err = tx.Exec("DELETE FROM usage_query_day_buckets WHERE day=?", key); err != nil {
		return err
	}
	for scope, bucket := range buckets {
		hours := Row{}
		for key, b := range bucket.hours {
			hours[key] = b.row()
		}
		if _, err = tx.Exec("INSERT INTO usage_query_day_buckets VALUES(?,?,?,?,?,?,?)", scope.client, boolInt(scope.local), key, bucket.first, bucket.last, dataJSON(bucket.total.row()), dataJSON(hours)); err != nil {
			return err
		}
	}
	return nil
}

func materializedChart(reader queryReader, q Query, start, end time.Time, granularity string) (map[string]*metricSum, map[string]int, time.Time, bool, error) {
	if !plainUsageFilter(q) || !end.After(start) {
		return nil, nil, time.Time{}, false, nil
	}
	tx, err := reader.DB.BeginTx(reader.ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, nil, time.Time{}, false, err
	}
	defer tx.Rollback()
	var ready bool
	if err = tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM usage_query_state WHERE key='go_projection'
		AND json_extract(data,'$.scope_version')=? AND json_extract(data,'$.bucket_calendar')=?)`,
		projectionScopeVersion, projectionCalendar(time.Now())).Scan(&ready); err != nil || !ready {
		return nil, nil, time.Time{}, false, err
	}
	field := "data"
	if granularity == "hour" {
		field = "hours"
	}
	lower := ""
	if !start.IsZero() {
		lower = start.In(time.Local).Format("2006-01-02")
	}
	client := normalizedClient(q.ClientKind)
	rows, err := tx.Query("SELECT first,last,"+field+" FROM usage_query_day_buckets WHERE client=? AND local=? AND day>=? AND day<=? ORDER BY day", client, boolInt(q.Source == "local"), lower, end.In(time.Local).Format("2006-01-02"))
	if err != nil {
		return nil, nil, time.Time{}, true, err
	}
	defer rows.Close()
	startStamp, endStamp := ledgerStamp(start), ledgerStamp(end)
	buckets, requests := map[string]*metricSum{}, map[string]int{}
	var earliest time.Time
	add := func(key string, value Row) {
		if buckets[key] == nil {
			buckets[key] = newMetricSum()
		}
		buckets[key].merge(value, client == "claude_code")
		requests[key] += int(ValueInt(value["user_requests"]))
	}
	for rows.Next() {
		var first, last, raw string
		if err = rows.Scan(&first, &last, &raw); err != nil {
			return nil, nil, time.Time{}, true, err
		}
		if first >= endStamp || !start.IsZero() && last < startStamp {
			continue
		}
		if !start.IsZero() && first < startStamp || last >= endStamp {
			// The exact legacy predicate is needed for a partial historical day
			// or a future record. Never publish a rounded time window.
			return nil, nil, time.Time{}, false, nil
		}
		t, ok := ParseStamp(first)
		if !ok {
			return nil, nil, time.Time{}, false, nil
		}
		if earliest.IsZero() || t.Before(earliest) {
			earliest = t
		}
		value, err := DecodeRow([]byte(raw))
		if err != nil {
			return nil, nil, time.Time{}, true, err
		}
		if granularity == "hour" {
			for key, cell := range value {
				add(key, ValueRow(cell))
			}
		} else {
			add(chartKey(t, granularity), value)
		}
	}
	return buckets, requests, earliest, true, rows.Err()
}
