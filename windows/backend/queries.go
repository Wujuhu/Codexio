package backend

import (
	"database/sql"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"
)

func queryBounds(q Query) (time.Time, time.Time) {
	now := time.Now()
	end := now
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.Local)
	start := day
	switch q.Period {
	case "all", "all_time":
		start = time.Time{}
	case "week":
		start = day.AddDate(0, 0, -6)
	case "month":
		start = day.AddDate(0, 0, -29)
	case "year":
		start = day.AddDate(-1, 0, 0)
	case "yesterday":
		start = day.AddDate(0, 0, -1)
		end = day
	}
	if t, ok := ParseStamp(q.Start); ok {
		start = t
	}
	if t, ok := ParseStamp(q.End); ok {
		end = t
		if len(q.End) == 10 {
			end = t.AddDate(0, 0, 1)
		}
		if end.After(now) {
			end = now
		}
	}
	return start, end
}
func queryFilter(q Query, groups bool) (string, []any) {
	start, end := queryBounds(q)
	where := "1=1"
	args := []any{}
	if !start.IsZero() {
		where += " AND timestamp>=?"
		args = append(args, ledgerStamp(start))
	}
	where += " AND timestamp<?"
	args = append(args, ledgerStamp(end))
	if q.Source == "local" {
		if groups {
			where += ` AND EXISTS(SELECT 1 FROM json_each(json_extract(data,'$.source_ids')) WHERE value='local' OR value LIKE 'local:%')`
		} else {
			where += ` AND EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=usage_priced_calls.id AND (s.source_id='local' OR s.source_id LIKE 'local:%'))`
		}
	}
	if q.Model != "" && q.Model != "all" {
		if groups {
			where += ` AND EXISTS(SELECT 1 FROM json_each(json_extract(data,'$.models')) WHERE value=?)`
		} else {
			where += " AND model=?"
		}
		args = append(args, q.Model)
	}
	if q.Tier != "" && q.Tier != "all" {
		where += ` AND json_extract(data,'$.service_tier')=?`
		tier := normalizedTier(q.Tier)
		if groups && q.Tier == "mixed" {
			tier = "mixed"
		}
		args = append(args, tier)
	}
	if q.Status != "" && q.Status != "all" {
		if groups {
			where += ` AND json_extract(data,'$.request_status')=?`
		} else {
			where += ` AND EXISTS(SELECT 1 FROM usage_request_members m JOIN usage_request_groups g ON m.request_id=g.id WHERE m.record_id=usage_priced_calls.id AND json_extract(g.data,'$.request_status')=?)`
		}
		args = append(args, q.Status)
	}
	if search := strings.TrimSpace(q.Search); search != "" {
		where += ` AND (instr(lower(coalesce(json_extract(data,'$.prompt_preview'),'')),lower(?))>0 OR instr(lower(coalesce(json_extract(data,'$.session_title'),'')),lower(?))>0 OR instr(lower(coalesce(json_extract(data,'$.model'),'')),lower(?))>0)`
		args = append(args, search, search, search)
	}
	return where, args
}
func (s *Store) Page(q Query) (PageResult, error) {
	groups := q.Mode != "model_call"
	table := "usage_priced_calls"
	if groups {
		table = "usage_request_groups"
	}
	where, args := queryFilter(q, groups)
	size := q.PageSize
	if size <= 0 {
		size = 50
	}
	if size > 100 {
		size = 100
	}
	page := q.Page
	if page < 1 {
		page = 1
	}
	result := PageResult{Rows: []Row{}, Page: page, Counts: map[string]int{}}
	if e := s.db.QueryRow("SELECT count(*) FROM "+table+" WHERE "+where, args...).Scan(&result.Total); e != nil {
		return result, e
	}
	result.Pages = (result.Total + size - 1) / size
	if result.Pages < 1 {
		result.Pages = 1
	}
	if page > result.Pages {
		page = result.Pages
		result.Page = page
	}
	params := append(append([]any{}, args...), size, (page-1)*size)
	rows, e := s.db.Query("SELECT data FROM "+table+" WHERE "+where+" ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?", params...)
	if e != nil {
		return result, e
	}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			rows.Close()
			return result, e
		}
		r := dataRow(raw)
		delete(r, "composition")
		if !groups {
			r["record_kind"] = "model_call"
		}
		result.Rows = append(result.Rows, r)
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return result, e
	}
	gw, ga := queryFilter(q, true)
	rows, e = s.db.Query("SELECT record_kind,count(*) FROM usage_request_groups WHERE "+gw+" GROUP BY record_kind", ga...)
	if e != nil {
		return result, e
	}
	for rows.Next() {
		var kind string
		var n int
		if e = rows.Scan(&kind, &n); e != nil {
			rows.Close()
			return result, e
		}
		key := map[string]string{"user_request": "requests", "subagent_request": "subagents", "automatic_approval_review": "reviews", "context_compaction": "compactions", "unassigned": "unassigned"}[kind]
		if key != "" {
			result.Counts[key] = n
		}
	}
	rows.Close()
	s.enrichUpstream(result.Rows, groups)
	return result, nil
}
func (s *Store) stream(q Query, fn func(Row)) error {
	where, args := queryFilter(q, false)
	rows, e := s.db.Query("SELECT data FROM usage_priced_calls WHERE "+where+" ORDER BY timestamp,id", args...)
	if e != nil {
		return e
	}
	defer rows.Close()
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			return e
		}
		fn(dataRow(raw))
	}
	return rows.Err()
}
func (s *Store) requestCount(q Query) (int, error) {
	where, args := queryFilter(q, true)
	var n int
	e := s.db.QueryRow("SELECT count(*) FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND "+where, args...).Scan(&n)
	return n, e
}
func (s *Store) Summary(q Query) (result Row, err error) {
	key, cached, ok := s.cachedQuery("summary", q)
	if ok {
		return cached, nil
	}
	defer func() {
		if err == nil {
			s.cacheQuery(key, result)
		}
	}()
	sum := newMetricSum()
	if e := s.stream(q, sum.add); e != nil {
		return nil, e
	}
	n, e := s.requestCount(q)
	if e != nil {
		return nil, e
	}
	r := sum.row()
	r["user_requests"] = n
	r["valid_counts"].(map[string]int)["user_requests"] = n
	r["skipped"].(map[string]int)["user_requests"] = 0
	return r, nil
}
func (s *Store) Recent(q Query, n int) ([]Row, error) {
	if n <= 0 {
		n = 3
	}
	if n > 100 {
		n = 100
	}
	where, args := queryFilter(q, true)
	args = append(args, n)
	rows, e := s.db.Query("SELECT data FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND "+where+" ORDER BY timestamp DESC,id DESC LIMIT ?", args...)
	if e != nil {
		return nil, e
	}
	result := []Row{}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			return nil, e
		}
		r := dataRow(raw)
		delete(r, "composition")
		result = append(result, r)
	}
	e = rows.Err()
	rows.Close()
	if e == nil {
		s.enrichUpstream(result, true)
	}
	return result, e
}
func chartKey(t time.Time, granularity string) string {
	t = t.In(time.Local)
	switch granularity {
	case "hour":
		return t.Format("2006-01-02T15:00:00-07:00")
	case "month":
		return t.Format("2006-01")
	case "week":
		year, week := t.ISOWeek()
		return fmt.Sprintf("%04d-W%02d", year, week)
	default:
		return t.Format("2006-01-02")
	}
}
func (s *Store) Chart(q Query) (resultRows []Row, err error) {
	key, cached, ok := s.cachedQuery("chart", q)
	if ok {
		return ValueRows(cached["rows"]), nil
	}
	defer func() {
		if err == nil {
			s.cacheQuery(key, Row{"rows": resultRows})
		}
	}()
	granularity := q.Granularity
	if granularity == "" {
		if q.Period == "today" || q.Period == "yesterday" || q.Period == "" {
			granularity = "hour"
		} else {
			granularity = "day"
		}
	}
	start, end := queryBounds(q)
	if start.IsZero() || end.Sub(start) > 730*24*time.Hour {
		granularity = "month"
	} else if granularity == "hour" && end.Sub(start) > 60*24*time.Hour {
		granularity = "day"
	}
	buckets := map[string]*metricSum{}
	requests := map[string]int{}
	if e := s.stream(q, func(r Row) {
		if t, ok := ParseStamp(r["timestamp"]); ok {
			k := chartKey(t, granularity)
			if buckets[k] == nil {
				buckets[k] = newMetricSum()
			}
			buckets[k].add(r)
		}
	}); e != nil {
		return nil, e
	}
	where, args := queryFilter(q, true)
	rows, e := s.db.Query("SELECT timestamp FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND "+where, args...)
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
			k := chartKey(t, granularity)
			requests[k]++
			if buckets[k] == nil {
				buckets[k] = newMetricSum()
			}
		}
	}
	rows.Close()
	keys := make([]string, 0, len(buckets))
	for key := range buckets {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	result := make([]Row, 0, len(keys))
	for _, key := range keys {
		r := buckets[key].row()
		r["timestamp"] = key
		r["date"] = key
		r["label"] = key
		r["granularity"] = granularity
		r["user_requests"] = requests[key]
		result = append(result, r)
	}
	return result, nil
}
func (s *Store) Models(q Query) (resultRows []Row, err error) {
	key, cached, ok := s.cachedQuery("models", q)
	if ok {
		return ValueRows(cached["rows"]), nil
	}
	defer func() {
		if err == nil {
			s.cacheQuery(key, Row{"rows": resultRows})
		}
	}()
	groups := map[string]*metricSum{}
	if e := s.stream(q, func(r Row) {
		model := firstString(r["model"], "unknown")
		if groups[model] == nil {
			groups[model] = newMetricSum()
		}
		groups[model].add(r)
	}); e != nil {
		return nil, e
	}
	result := []Row{}
	for model, totals := range groups {
		r := totals.row()
		r["model"] = model
		r["call_count"] = r["requests"]
		mq := q
		mq.Model = model
		n, e := s.requestCount(mq)
		if e != nil {
			return nil, e
		}
		r["user_requests"] = n
		result = append(result, r)
	}
	sort.Slice(result, func(i, j int) bool { return ValueInt(result[i]["tokens"]) > ValueInt(result[j]["tokens"]) })
	return result, nil
}
func (s *Store) Insights(q Query) (result Row, err error) {
	size := q.PageSize
	if size <= 0 || size > 50 {
		size = 5
	}
	page := max(1, q.Page)
	key, cached, ok := s.cachedQuery(fmt.Sprintf("insights:%d:%d", page, size), q)
	if ok {
		return cached, nil
	}
	defer func() {
		if err == nil {
			s.cacheQuery(key, result)
		}
	}()
	where, args := queryFilter(q, true)
	var total int
	if err = s.db.QueryRow(`SELECT count(DISTINCT json_extract(data,'$.session_id')) FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND `+where, args...).Scan(&total); err != nil {
		return nil, err
	}
	pages := max(1, (total+size-1)/size)
	page = min(page, pages)
	params := append(append([]any{}, args...), size, (page-1)*size)
	rows, e := s.db.Query(`SELECT json_extract(data,'$.session_id'),max(json_extract(data,'$.session_title')),count(*),sum(json_extract(data,'$.total_tokens')),sum(json_extract(data,'$.cost_usd')),sum(CASE WHEN json_extract(data,'$.cost_partial') OR json_extract(data,'$.unpriced_calls')>0 OR json_extract(data,'$.cost_usd') IS NULL THEN 1 ELSE 0 END) FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND `+where+` GROUP BY json_extract(data,'$.session_id') ORDER BY count(*) DESC,json_extract(data,'$.session_id') LIMIT ? OFFSET ?`, params...)
	if e != nil {
		return nil, e
	}
	chats := []Row{}
	for rows.Next() {
		var session string
		var title sql.NullString
		var requests int
		var tokens sql.NullInt64
		var cost sql.NullFloat64
		var partial int
		if e = rows.Scan(&session, &title, &requests, &tokens, &cost, &partial); e != nil {
			rows.Close()
			return nil, e
		}
		r := Row{"session_id": session, "session_title": title.String, "name": title.String, "user_requests": requests, "tokens": nil, "usd": nil, "cost_partial": partial > 0, "cost_complete": partial == 0}
		if tokens.Valid {
			r["tokens"] = tokens.Int64
		}
		if cost.Valid {
			r["usd"] = cost.Float64
		}
		chats = append(chats, r)
	}
	rows.Close()
	local, e := s.localInsights(q)
	if e != nil {
		return nil, e
	}
	return Row{"chats": chats, "chat_rankings": chats, "chat_page": page, "chat_pages": pages, "chat_total": total, "stats": local["stats"], "activity": local["activity"], "heatmap": local["activity"]}, nil
}
func (s *Store) Filters() (Row, error) {
	result := Row{"models": []string{}, "tiers": []string{}, "statuses": []string{"running", "completed", "aborted", "unknown"}}
	for _, field := range []string{"model", "tier"} {
		rows, e := s.db.Query("SELECT DISTINCT " + field + " FROM usage_priced_calls ORDER BY " + field + " LIMIT 500")
		if e != nil {
			return nil, e
		}
		values := []string{}
		for rows.Next() {
			var v string
			if e = rows.Scan(&v); e != nil {
				rows.Close()
				return nil, e
			}
			values = append(values, v)
		}
		rows.Close()
		key := "models"
		if field == "tier" {
			key = "tiers"
		}
		result[key] = values
	}
	return result, nil
}
func (s *Store) Detail(id string, page int) (Row, error) {
	if id == "" || len(id) > 1024 {
		return nil, errors.New("invalid request identity")
	}
	if page < 1 {
		page = 1
	}
	var raw string
	e := s.db.QueryRow("SELECT data FROM usage_request_groups WHERE id=?", id).Scan(&raw)
	call := false
	if e == sql.ErrNoRows {
		e = s.db.QueryRow("SELECT data FROM usage_priced_calls WHERE id=?", id).Scan(&raw)
		call = true
	}
	if e != nil {
		return nil, e
	}
	request := dataRow(raw)
	result := Row{"request": request, "user": request["prompt_preview"], "final": request["output_preview"], "attachments": []Row{}, "members": []Row{}, "composition": request["composition"], "page": page, "pages": 1, "user_complete": false, "final_complete": false, "availability": "legacy_preview"}
	messageID := id
	if call {
		messageID = turnKey(dataString(request, "session_id"), firstString(request["request_turn_id"], request["turn_id"]))
	}
	var message string
	if s.db.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", messageID).Scan(&message) == nil {
		m := dataRow(message)
		for _, k := range []string{"user", "final", "attachments", "user_complete", "final_complete", "availability"} {
			if v, ok := m[k]; ok {
				result[k] = v
			}
		}
		if m["availability"] == nil {
			result["availability"] = "source"
		}
		_, _ = s.db.Exec("UPDATE usage_request_messages SET accessed=? WHERE id=?", float64(time.Now().Unix()), messageID)
	} else if recovered := s.sourceMessage(request); len(recovered) > 0 {
		for k, v := range recovered {
			result[k] = v
		}
		tx, e := s.db.Begin()
		if e == nil {
			if e = saveMessage(tx, messageID, recovered); e == nil {
				_ = tx.Commit()
			} else {
				_ = tx.Rollback()
			}
		}
	}
	if !call {
		var continued string
		_ = s.db.QueryRow(`SELECT m.data FROM usage_query_turns t JOIN usage_request_messages m ON t.id=m.id WHERE json_extract(t.data,'$.root_id')=? AND coalesce(json_extract(t.data,'$.continuation_of'),'')<>'' AND coalesce(json_extract(t.data,'$.is_subagent'),0)=0 AND coalesce(json_extract(m.data,'$.final'),'')<>'' ORDER BY json_extract(t.data,'$.ended_at') DESC LIMIT 1`, id).Scan(&continued)
		if continued != "" {
			m := dataRow(continued)
			result["final"] = m["final"]
			result["final_complete"] = m["final_complete"]
		}
	}
	var count int
	if call {
		count = 1
		result["members"] = []Row{request}
	} else {
		if e = s.db.QueryRow("SELECT count(*) FROM usage_request_members WHERE request_id=?", id).Scan(&count); e != nil {
			return nil, e
		}
		pages := (count + 49) / 50
		if pages < 1 {
			pages = 1
		}
		if page > pages {
			page = pages
			result["page"] = page
		}
		result["pages"] = pages
		rows, e := s.db.Query("SELECT p.data FROM usage_priced_calls p JOIN usage_request_members m ON p.id=m.record_id WHERE m.request_id=? ORDER BY p.timestamp,p.id LIMIT 50 OFFSET ?", id, (page-1)*50)
		if e != nil {
			return nil, e
		}
		members := []Row{}
		for rows.Next() {
			var raw string
			if e = rows.Scan(&raw); e != nil {
				rows.Close()
				return nil, e
			}
			members = append(members, dataRow(raw))
		}
		rows.Close()
		result["members"] = members
	}
	result["total_members"] = count
	s.enrichUpstream([]Row{request}, !call)
	s.enrichUpstream(ValueRows(result["members"]), false)
	// Continuation replies may advance the final output; agent/review replies never do.
	composition := ValueRows(request["composition"])
	if len(composition) > 100 {
		result["composition"] = composition[:100]
		result["composition_truncated"] = true
	}
	delete(request, "composition")
	return result, nil
}
