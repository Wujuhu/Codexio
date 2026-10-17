package backend

import (
	"context"
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
	where := "coalesce(json_extract(data,'$.client_kind'),'codex')=?"
	args := []any{normalizedClient(q.ClientKind)}
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
			if normalizedClient(q.ClientKind) == "claude_code" {
				where += " AND (model=? OR json_extract(data,'$.returned_model')=?)"
				args = append(args, q.Model)
			} else {
				where += " AND model=?"
			}
		}
		args = append(args, q.Model)
	}
	if q.Tier != "" && q.Tier != "all" {
		aliases := map[string][]string{"default": {"default", "standard"}, "priority": {"priority", "fast"}, "ultrafast": {"ultrafast", "ultra_fast", "ultra-fast"}, "mixed": {"mixed"}, "unknown": {"unknown"}}[requestSpeed(q.Tier)]
		where += ` AND lower(trim(json_extract(data,'$.service_tier'))) IN (` + strings.TrimSuffix(strings.Repeat("?,", len(aliases)), ",") + `)`
		for _, tier := range aliases {
			args = append(args, tier)
		}
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
		where += ` AND (instr(lower(coalesce(json_extract(data,'$.prompt_preview'),'')),lower(?))>0 OR instr(lower(coalesce(json_extract(data,'$.session_title'),'')),lower(?))>0 OR instr(lower(coalesce(json_extract(data,'$.model'),'')),lower(?))>0`
		args = append(args, search, search, search)
		if normalizedClient(q.ClientKind) == "claude_code" {
			matched := `SELECT l.item_id FROM claude_body_search JOIN claude_search b ON b.rowid=claude_body_search.rowid JOIN claude_locators l ON l.uuid=b.uuid AND l.path=b.path WHERE claude_body_search MATCH ? AND l.available=1 AND l.generation=b.generation`
			if groups {
				where += ` OR id IN (` + matched + `) OR id IN(SELECT request_id FROM usage_request_members WHERE record_id IN (` + matched + `))`
			} else {
				where += ` OR id IN (` + matched + `) OR json_extract(data,'$.claude_request_id') IN (` + matched + `)`
			}
			match := `"` + strings.ReplaceAll(search, `"`, `""`) + `"*`
			args = append(args, match, match)
		}
		where += ")"
	}
	return where, args
}
func (s *Store) Page(q Query, contexts ...context.Context) (PageResult, error) {
	reader := s.readDB(contexts)
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
	result := PageResult{Rows: []Row{}, Page: page, Counts: map[string]int{}, State: s.ClientStatus(q.ClientKind)}
	var firstStamp sql.NullString
	if e := reader.QueryRow("SELECT count(*),min(timestamp) FROM "+table+" WHERE "+where, args...).Scan(&result.Total, &firstStamp); e != nil {
		return result, e
	}
	// Use the complete filtered range so every page shares the same date format.
	start, end := queryBounds(q)
	if start.IsZero() && firstStamp.Valid {
		start, _ = ParseStamp(firstStamp.String)
	}
	year := time.Now().Year()
	result.ShowYear = (!start.IsZero() && start.In(time.Local).Year() != year) || end.In(time.Local).Year() != year
	result.Pages = (result.Total + size - 1) / size
	if result.Pages < 1 {
		result.Pages = 1
	}
	if page > result.Pages {
		page = result.Pages
		result.Page = page
	}
	params := append(append([]any{}, args...), size, (page-1)*size)
	rows, e := reader.Query("SELECT data FROM "+table+" WHERE "+where+" ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?", params...)
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
	rows, e = reader.Query("SELECT record_kind,count(*) FROM usage_request_groups WHERE "+gw+" GROUP BY record_kind", ga...)
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
	e = rows.Err()
	rows.Close()
	if e != nil {
		return result, e
	}
	if e = s.claudeSourceFields(result.Rows, reader.ctx); e != nil {
		return result, e
	}
	if normalizedClient(q.ClientKind) == "codex" {
		s.enrichUpstream(result.Rows, groups, reader.ctx)
	}
	return result, nil
}
func (s *Store) stream(q Query, fn func(Row), contexts ...context.Context) error {
	reader := s.readDB(contexts)
	where, args := queryFilter(q, false)
	rows, e := reader.Query("SELECT data FROM usage_priced_calls WHERE "+where+" ORDER BY timestamp,id", args...)
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
func (s *Store) requestCount(q Query, contexts ...context.Context) (int, error) {
	reader := s.readDB(contexts)
	where, args := queryFilter(q, true)
	var n int
	e := reader.QueryRow("SELECT count(*) FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND "+where, args...).Scan(&n)
	return n, e
}
func (s *Store) Summary(q Query, contexts ...context.Context) (result Row, err error) {
	reader := s.readDB(contexts)
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
	if e := s.stream(q, sum.add, reader.ctx); e != nil {
		return nil, e
	}
	n, e := s.requestCount(q, reader.ctx)
	if e != nil {
		return nil, e
	}
	r := sum.row()
	r["user_requests"] = n
	r["valid_counts"].(map[string]int)["user_requests"] = n
	r["skipped"].(map[string]int)["user_requests"] = 0
	return r, nil
}
func (s *Store) Recent(q Query, n int, contexts ...context.Context) ([]Row, error) {
	reader := s.readDB(contexts)
	if n <= 0 {
		n = 3
	}
	if n > 100 {
		n = 100
	}
	where, args := queryFilter(q, true)
	args = append(args, n)
	rows, e := reader.Query("SELECT data FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND "+where+" ORDER BY timestamp DESC,id DESC LIMIT ?", args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	result := []Row{}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			rows.Close()
			return nil, e
		}
		r := dataRow(raw)
		delete(r, "composition")
		result = append(result, r)
	}
	e = rows.Err()
	rows.Close()
	if e == nil && normalizedClient(q.ClientKind) == "codex" {
		s.enrichUpstream(result, true, reader.ctx)
	}
	if e == nil {
		e = s.claudeSourceFields(result, reader.ctx)
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

func chartBucketKeys(start, end time.Time, granularity string) []string {
	if start.IsZero() || !end.After(start) || end.Sub(start) > 730*24*time.Hour {
		return nil
	}
	location := time.Local
	start = start.In(location)
	switch granularity {
	case "hour":
		start = time.Date(start.Year(), start.Month(), start.Day(), start.Hour(), 0, 0, 0, location)
	case "week":
		start = time.Date(start.Year(), start.Month(), start.Day(), 0, 0, 0, 0, location)
		start = start.AddDate(0, 0, -(int(start.Weekday())+6)%7)
	case "month":
		start = time.Date(start.Year(), start.Month(), 1, 0, 0, 0, 0, location)
	default:
		start = time.Date(start.Year(), start.Month(), start.Day(), 0, 0, 0, 0, location)
	}
	result := []string{}
	seen := map[string]bool{}
	for cursor := start; cursor.Before(end); {
		key := chartKey(cursor, granularity)
		if !seen[key] {
			result = append(result, key)
			seen[key] = true
		}
		switch granularity {
		case "hour":
			cursor = cursor.Add(time.Hour)
		case "week":
			cursor = cursor.AddDate(0, 0, 7)
		case "month":
			cursor = cursor.AddDate(0, 1, 0)
		default:
			cursor = cursor.AddDate(0, 0, 1)
		}
	}
	return result
}

func emptyChartMetric() Row {
	r := newMetricSum().row()
	for _, key := range append([]string{"tokens", "requests"}, tokenFields...) {
		r[key] = int64(0)
	}
	r["usd"] = float64(0)
	return r
}

func (s *Store) Chart(q Query, contexts ...context.Context) (resultRows []Row, err error) {
	reader := s.readDB(contexts)
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
	firstBucket := time.Time{}
	if e := s.stream(q, func(r Row) {
		if t, ok := ParseStamp(r["timestamp"]); ok {
			if firstBucket.IsZero() || t.Before(firstBucket) {
				firstBucket = t
			}
			k := chartKey(t, granularity)
			if buckets[k] == nil {
				buckets[k] = newMetricSum()
			}
			buckets[k].add(r)
		}
	}, reader.ctx); e != nil {
		return nil, e
	}
	where, args := queryFilter(q, true)
	rows, e := reader.Query("SELECT timestamp FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 AND "+where, args...)
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
			if firstBucket.IsZero() || t.Before(firstBucket) {
				firstBucket = t
			}
			k := chartKey(t, granularity)
			requests[k]++
			if buckets[k] == nil {
				buckets[k] = newMetricSum()
			}
		}
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return nil, e
	}
	bucketStart := start
	if bucketStart.IsZero() {
		bucketStart = firstBucket
	}
	keys := chartBucketKeys(bucketStart, end, granularity)
	known := map[string]bool{}
	for _, key := range keys {
		known[key] = true
	}
	for key := range buckets {
		if !known[key] {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	result := make([]Row, 0, len(keys))
	for _, key := range keys {
		r := emptyChartMetric()
		if buckets[key] != nil && buckets[key].records > 0 {
			r = buckets[key].row()
		}
		if normalizedClient(q.ClientKind) == "claude_code" {
			r["client_kind"] = "claude_code"
		}
		r["timestamp"] = key
		r["date"] = key
		r["label"] = key
		r["granularity"] = granularity
		r["user_requests"] = requests[key]
		result = append(result, r)
	}
	return result, nil
}
func (s *Store) Models(q Query, contexts ...context.Context) (resultRows []Row, err error) {
	reader := s.readDB(contexts)
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
	}, reader.ctx); e != nil {
		return nil, e
	}
	result := []Row{}
	for model, totals := range groups {
		r := totals.row()
		r["model"] = model
		r["call_count"] = r["requests"]
		mq := q
		mq.Model = model
		n, e := s.requestCount(mq, reader.ctx)
		if e != nil {
			return nil, e
		}
		r["user_requests"] = n
		result = append(result, r)
	}
	sort.Slice(result, func(i, j int) bool { return ValueInt(result[i]["tokens"]) > ValueInt(result[j]["tokens"]) })
	return result, nil
}
func (s *Store) Insights(q Query, contexts ...context.Context) (result Row, err error) {
	reader := s.readDB(contexts)
	size := q.PageSize
	if size <= 0 {
		size = 5
	}
	if size > 25 {
		size = 25
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
	chats, total, page, e := s.localChatRows(q, page, size, reader.ctx)
	if e != nil {
		return nil, e
	}
	pages := max(1, (total+size-1)/size)
	local, e := s.localInsights(Query{ClientKind: q.ClientKind, Period: "all", Source: "local"}, reader.ctx)
	if e != nil {
		return nil, e
	}
	return Row{"chats": chats, "chat_rankings": chats, "chat_page": page, "chat_pages": pages, "chat_total": total, "stats": local["stats"], "activity": local["activity"], "heatmap": local["activity"]}, nil
}
func (s *Store) Filters() (Row, error) { return s.ClientFilters("codex") }
func (s *Store) ClientFilters(client string, contexts ...context.Context) (Row, error) {
	reader := s.readDB(contexts)
	result := Row{"models": []string{}, "tiers": []string{}, "statuses": []string{"running", "completed", "aborted", "unknown"}}
	for _, field := range []string{"model", "tier"} {
		statement := "SELECT DISTINCT " + field + " FROM usage_priced_calls WHERE coalesce(json_extract(data,'$.client_kind'),'codex')=? ORDER BY " + field + " LIMIT 500"
		if normalizedClient(client) == "claude_code" && field == "model" {
			statement = "SELECT DISTINCT model FROM (SELECT model FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')=? UNION SELECT json_extract(data,'$.returned_model') AS model FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')='claude_code') WHERE model IS NOT NULL AND model<>'' ORDER BY model LIMIT 500"
		}
		rows, e := reader.Query(statement, normalizedClient(client))
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
		e = rows.Err()
		rows.Close()
		if e != nil {
			return nil, e
		}
		key := "models"
		if field == "tier" {
			key = "tiers"
		}
		result[key] = values
	}
	return result, nil
}
func (s *Store) Detail(id string, page int, contexts ...context.Context) (Row, error) {
	reader := s.readDB(contexts)
	if id == "" || len(id) > 1024 {
		return nil, errors.New("invalid request identity")
	}
	if page < 1 {
		page = 1
	}
	var raw string
	e := reader.QueryRow("SELECT data FROM usage_request_groups WHERE id=?", id).Scan(&raw)
	call := false
	if e == sql.ErrNoRows {
		e = reader.QueryRow("SELECT data FROM usage_priced_calls WHERE id=?", id).Scan(&raw)
		call = true
	}
	if e != nil {
		return nil, e
	}
	request := dataRow(raw)
	if normalizedClient(dataString(request, "client_kind")) == "claude_code" {
		return s.claudeDetail(request, call, page, reader.ctx)
	}
	result := Row{"request": request, "user": request["prompt_preview"], "final": request["output_preview"], "attachments": []Row{}, "members": []Row{}, "composition": request["composition"], "page": page, "pages": 1, "user_complete": false, "final_complete": false, "availability": "legacy_preview"}
	var message Row
	if call {
		message = s.sourceMessage(request, reader.ctx)
	} else {
		message, e = s.requestMessageDetail(request, true, reader.ctx)
		if e != nil {
			return nil, e
		}
	}
	for _, k := range []string{"user", "final", "attachments", "user_complete", "final_complete", "availability"} {
		if v, ok := message[k]; ok {
			result[k] = v
		}
	}
	var count int
	if call {
		count = 1
		result["members"] = []Row{request}
	} else {
		if e = reader.QueryRow("SELECT count(*) FROM usage_request_members WHERE request_id=?", id).Scan(&count); e != nil {
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
		rows, e := reader.Query("SELECT p.data FROM usage_priced_calls p JOIN usage_request_members m ON p.id=m.record_id WHERE m.request_id=? ORDER BY p.timestamp,p.id LIMIT 50 OFFSET ?", id, (page-1)*50)
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
		e = rows.Err()
		rows.Close()
		if e != nil {
			return nil, e
		}
		result["members"] = members
	}
	result["total_members"] = count
	s.enrichUpstream([]Row{request}, !call, reader.ctx)
	s.enrichUpstream(ValueRows(result["members"]), false, reader.ctx)
	// Continuation replies may advance the final output; agent/review replies never do.
	composition := ValueRows(request["composition"])
	if len(composition) > 100 {
		result["composition"] = composition[:100]
		result["composition_truncated"] = true
	}
	delete(request, "composition")
	return result, nil
}
