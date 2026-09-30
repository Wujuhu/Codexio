package backend

// Local activity follows local_activity.py: mode percentages use confirmed
// model calls, while heatmap requests use the shared true-user classification.
import (
	"database/sql"
	"sort"
	"strings"
	"time"
)

func (s *Store) localInsights(q Query) (result Row, err error) {
	q.Source = "local"
	key, cached, ok := s.cachedQuery("local-activity", q)
	if ok {
		return cached, nil
	}
	defer func() {
		if err == nil {
			s.cacheQuery(key, result)
		}
	}()
	now := time.Now()
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.Local)
	floor := day.AddDate(0, 0, -364)
	daily := map[string]*metricSum{}
	requests := map[string]int{}
	active := map[string]bool{}
	knownDaily := map[string]int64{}
	total := newMetricSum()
	speeds, efforts := map[string]int{}, map[string]int{}
	err = s.stream(q, func(r Row) {
		beforeTokens, beforeValid := total.totals["tokens"], total.valid["tokens"]
		total.add(r)
		t, ok := ParseStamp(r["timestamp"])
		if !ok {
			return
		}
		date := t.In(time.Local).Format("2006-01-02")
		if total.valid["tokens"] > beforeValid {
			knownDaily[date] += total.totals["tokens"] - beforeTokens
		}
		if !t.Before(floor) {
			if daily[date] == nil {
				daily[date] = newMetricSum()
			}
			daily[date].add(r)
		}
		quality := strings.Split(dataString(r, "quality"), ":")[0]
		confirmed := quality == "" || quality == "response" || quality == "legacy_last" || quality == "cumulative_delta" && strings.HasPrefix(dataString(r, "id"), "response:")
		if confirmed {
			active[date] = true
			tier := normalizedTier(r["service_tier"])
			speeds[tier]++
			effort := dataString(r, "reasoning_effort")
			if effort == "" {
				effort = "unknown"
			}
			efforts[effort]++
		}
	})
	if err != nil {
		return nil, err
	}
	where, args := queryFilter(q, true)
	rows, e := s.db.Query(`SELECT timestamp FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND `+where, args...)
	if e != nil {
		return nil, e
	}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			rows.Close()
			return nil, e
		}
		if t, ok := ParseStamp(raw); ok {
			requests[t.In(time.Local).Format("2006-01-02")]++
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	calendar := make([]Row, 0, 365)
	var peak any
	for _, tokens := range knownDaily {
		if prior, ok := knownCount(peak); !ok || tokens > prior {
			peak = tokens
		}
	}
	for offset := 0; offset < 365; offset++ {
		date := floor.AddDate(0, 0, offset).Format("2006-01-02")
		m := daily[date]
		r := Row{"tokens": 0, "usd": 0, "cost_complete": true, "cache_hit_rate": nil}
		if m != nil {
			r = m.row()
		}
		r["date"] = date
		r["timestamp"] = date
		r["label"] = date
		r["user_requests"] = requests[date]
		calendar = append(calendar, r)
	}
	dates := []string{}
	for date := range active {
		dates = append(dates, date)
	}
	sort.Strings(dates)
	longest, streak := 0, 0
	var previous time.Time
	for _, date := range dates {
		t, ok := ParseStamp(date)
		if !ok {
			continue
		}
		if !previous.IsZero() && previous.AddDate(0, 0, 1).Equal(t) {
			streak++
		} else {
			streak = 1
		}
		longest = max(longest, streak)
		previous = t
	}
	cursor := day
	if !active[cursor.Format("2006-01-02")] {
		cursor = cursor.AddDate(0, 0, -1)
	}
	current := 0
	for active[cursor.Format("2006-01-02")] {
		current++
		cursor = cursor.AddDate(0, 0, -1)
	}
	calls := speeds["default"] + speeds["priority"] + speeds["unknown"]
	var fast, effortPercent any
	var most any
	mostCount := 0
	effortKeys := []string{}
	for effort := range efforts {
		effortKeys = append(effortKeys, effort)
	}
	sort.Strings(effortKeys)
	for _, effort := range effortKeys {
		if effort != "unknown" && efforts[effort] > mostCount {
			most = effort
			mostCount = efforts[effort]
		}
	}
	if calls > 0 {
		fast = float64(speeds["priority"]) * 100 / float64(calls)
		if most != nil {
			effortPercent = float64(mostCount) * 100 / float64(calls)
		}
	}
	seconds, partial, e := s.localChatDuration(q)
	if e != nil {
		return nil, e
	}
	stats := Row{"total_tokens": total.row()["tokens"], "peak_daily_tokens": peak, "longest_chat_seconds": seconds, "duration_partial": partial, "current_streak_days": current, "longest_streak_days": longest, "fast_percent": fast, "most_used_effort": most, "effort_percent": effortPercent, "unknown_speed": speeds["unknown"], "unknown_effort": efforts["unknown"], "calls": calls}
	return Row{"stats": stats, "activity": calendar}, nil
}

func (s *Store) localChatDuration(q Query) (any, bool, error) {
	where, args := queryFilter(q, false)
	rows, err := s.db.Query(`SELECT coalesce((SELECT json_extract(g.data,'$.session_id') FROM usage_request_groups g WHERE g.id=json_extract(t.data,'$.root_id')),json_extract(t.data,'$.session_id')),json_extract(t.data,'$.started_at'),json_extract(t.data,'$.ended_at'),json_extract(t.data,'$.duration_ms'),coalesce(json_extract(t.data,'$.started_inferred'),1),json_extract(t.data,'$.status') FROM usage_query_turns t WHERE EXISTS(SELECT 1 FROM usage_priced_calls WHERE session_id=json_extract(t.data,'$.session_id') AND turn_id=json_extract(t.data,'$.turn_id') AND `+where+`) ORDER BY 1,CASE WHEN (julianday(json_extract(t.data,'$.started_at')) IS NULL OR coalesce(json_extract(t.data,'$.started_inferred'),1)<>0) AND json_extract(t.data,'$.duration_ms')>=0 THEN julianday(json_extract(t.data,'$.ended_at'))-json_extract(t.data,'$.duration_ms')/86400000.0 ELSE julianday(json_extract(t.data,'$.started_at')) END`, args...)
	if err != nil {
		return nil, false, err
	}
	defer rows.Close()
	partial := false
	var maximum any
	var session string
	var start, end time.Time
	seconds := 0.0
	finish := func() {
		if !start.IsZero() {
			seconds += end.Sub(start).Seconds()
		}
		if n, ok := ValueFloat(maximum); !ok || seconds > n {
			if seconds > 0 {
				maximum = seconds
			}
		}
		start = time.Time{}
		end = time.Time{}
		seconds = 0
	}
	for rows.Next() {
		var id string
		var from, to, status sql.NullString
		var duration sql.NullFloat64
		var inferred bool
		if err = rows.Scan(&id, &from, &to, &duration, &inferred, &status); err != nil {
			return nil, false, err
		}
		if session != "" && session != id {
			finish()
		}
		session = id
		a, aok := ParseStamp(from.String)
		b, bok := ParseStamp(to.String)
		if bok && (!aok || inferred) && duration.Valid && duration.Float64 >= 0 {
			a = b.Add(-time.Duration(duration.Float64 * float64(time.Millisecond)))
			aok = true
		}
		if inferred && (!duration.Valid || duration.Float64 < 0) || !aok || !bok || b.Before(a) || b.After(time.Now()) || (status.String != "completed" && status.String != "aborted") {
			partial = true
			continue
		}
		if start.IsZero() {
			start = a
			end = b
		} else if !a.After(end) {
			if b.After(end) {
				end = b
			}
		} else {
			seconds += end.Sub(start).Seconds()
			start = a
			end = b
		}
	}
	finish()
	return maximum, partial, rows.Err()
}
