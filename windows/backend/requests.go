package backend

import (
	"database/sql"
	"sort"
	"strings"
	"time"
)

// Groups are a derived index. Durable response IDs and meter rows are never rewritten here.
type requestBase struct {
	meta  Row
	calls []Row
	note  string
}

func projectionDirtySessions(tx *sql.Tx, expired bool) (map[string]bool, error) {
	result := map[string]bool{}
	rows, e := tx.Query(`SELECT session_id FROM usage_records WHERE id IN(SELECT item_id FROM usage_query_changes WHERE kind='record') UNION SELECT session_id FROM usage_priced_calls WHERE id IN(SELECT item_id FROM usage_query_changes WHERE kind='record') UNION SELECT item_id FROM usage_query_changes WHERE kind='title'`)
	if e != nil {
		return nil, e
	}
	for rows.Next() {
		var session string
		if e = rows.Scan(&session); e != nil {
			rows.Close()
			return nil, e
		}
		result[session] = true
	}
	rows.Close()
	changes, e := tx.Query("SELECT DISTINCT kind,item_id FROM usage_query_changes WHERE kind IN ('turn','agent')")
	if e != nil {
		return nil, e
	}
	items := [][2]string{}
	for changes.Next() {
		var kind, id string
		if e = changes.Scan(&kind, &id); e != nil {
			changes.Close()
			return nil, e
		}
		items = append(items, [2]string{kind, id})
	}
	changes.Close()
	for _, item := range items {
		var raw string
		if item[0] == "turn" {
			_ = tx.QueryRow("SELECT data FROM usage_turns WHERE id=?", item[1]).Scan(&raw)
			r := dataRow(raw)
			if session := dataString(r, "session_id"); session != "" {
				result[session] = true
			} else if i := strings.Index(item[1], "|turn:"); i >= 0 {
				part := item[1][i+6:]
				if j := strings.LastIndex(part, ":"); j >= 0 {
					result[part[:j]] = true
				}
			}
		} else {
			_ = tx.QueryRow("SELECT data FROM usage_agent_links WHERE id=?", item[1]).Scan(&raw)
			r := dataRow(raw)
			result[dataString(r, "parent_session_id")] = true
			result[dataString(r, "child_session_id")] = true
		}
	}
	if expired {
		rows, e := tx.Query(`SELECT DISTINCT json_extract(data,'$.session_id') FROM usage_request_groups WHERE json_extract(data,'$.request_status')='running'`)
		if e != nil {
			return nil, e
		}
		for rows.Next() {
			var session sql.NullString
			if e = rows.Scan(&session); e != nil {
				rows.Close()
				return nil, e
			}
			if session.Valid {
				result[session.String] = true
			}
		}
		rows.Close()
	}
	return result, nil
}

func queryJSON(tx *sql.Tx, q string, args ...any) ([]Row, error) {
	rows, e := tx.Query(q, args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	result := []Row{}
	for rows.Next() {
		var raw string
		if e = rows.Scan(&raw); e != nil {
			return nil, e
		}
		result = append(result, dataRow(raw))
	}
	return result, rows.Err()
}
func (s *Store) project() error {
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	var revision int64
	_ = tx.QueryRow("SELECT revision FROM usage_revisions WHERE kind='ledger'").Scan(&revision)
	var prior string
	_ = tx.QueryRow("SELECT data FROM usage_query_state WHERE key='go_projection'").Scan(&prior)
	state := dataRow(prior)
	s.mu.RLock()
	version := s.priceVersion
	s.mu.RUnlock()
	priceChanged := dataString(state, "prices") != version
	full := prior == "" || priceChanged
	groupFull := full || ValueInt(state["request_projection_version"]) != 1
	// Running status expires without rescanning history. Future rows are filtered by query time.
	now := time.Now()
	expired := false
	var stale int
	_ = tx.QueryRow(`SELECT count(*) FROM usage_request_groups WHERE json_extract(data,'$.request_status')='running' AND timestamp<?`, ledgerStamp(now.Add(-24*time.Hour))).Scan(&stale)
	expired = stale > 0
	if !groupFull && ValueInt(state["revision"]) == revision && !expired {
		if s.Generation() == 0 {
			s.generation.Store(ValueInt(state["generation"]))
		}
		return nil
	}
	dirty, e := projectionDirtySessions(tx, expired)
	if e != nil {
		return e
	}
	changedSQL := "SELECT r.data FROM usage_records r"
	if !full {
		changedSQL += ` WHERE (r.id IN (SELECT item_id FROM usage_query_changes WHERE kind='record') OR r.session_id IN (SELECT item_id FROM usage_query_changes WHERE kind='title'))`
	}
	// Bounded input batches; SQL holds the complete history rather than a UI snapshot.
	last := ""
	for {
		q := changedSQL
		if strings.Contains(q, " WHERE ") {
			q += " AND r.id>?"
		} else {
			q += " WHERE r.id>?"
		}
		q += " ORDER BY r.id LIMIT 500"
		records, e := queryJSON(tx, q, last)
		if e != nil {
			return e
		}
		if len(records) == 0 {
			break
		}
		for _, r := range records {
			if timestamp := stamp(r["timestamp"]); timestamp != "" {
				r["timestamp"] = timestamp
			}
			last = dataString(r, "id")
			var title string
			_ = tx.QueryRow("SELECT title FROM usage_session_titles WHERE session_id=?", r["session_id"]).Scan(&title)
			if title != "" {
				r["session_title"] = title
			}
			rows, e := tx.Query("SELECT source_id FROM usage_record_sources WHERE record_id=? ORDER BY source_id", r["id"])
			if e != nil {
				return e
			}
			sources := []string{}
			for rows.Next() {
				var source string
				_ = rows.Scan(&source)
				sources = append(sources, source)
			}
			rows.Close()
			r["source_ids"] = sources
			if _, e = tx.Exec("DELETE FROM usage_query_sources WHERE record_id=?", r["id"]); e != nil {
				return e
			}
			if _, e = tx.Exec("INSERT INTO usage_query_sources SELECT record_id,source_id FROM usage_record_sources WHERE record_id=?", r["id"]); e != nil {
				return e
			}
			p := s.price(r)
			metrics := metricRow(p)
			_, e = tx.Exec(`INSERT INTO usage_priced_calls VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,model=excluded.model,tier=excluded.tier,source_id=excluded.source_id,session_id=excluded.session_id,turn_id=excluded.turn_id,metrics=excluded.metrics,data=excluded.data`, p["id"], p["timestamp"], firstString(p["model"], "unknown"), normalizedTier(p["service_tier"]), dataString(p, "source_id"), dataString(p, "session_id"), firstString(p["request_turn_id"], p["turn_id"]), dataJSON(metrics), dataJSON(p))
			if e != nil {
				return e
			}
		}
		if len(records) < 500 {
			break
		}
	}
	if _, e = tx.Exec("DELETE FROM usage_priced_calls WHERE id NOT IN(SELECT id FROM usage_records)"); e != nil {
		return e
	}
	if _, e = tx.Exec("DELETE FROM usage_query_sources WHERE record_id NOT IN(SELECT id FROM usage_records)"); e != nil {
		return e
	}
	// Metadata graph is rebuilt only after ledger/price dependencies change. Calls
	// are streamed per connected session component, bounding historical meter memory.
	if e = s.rebuildGroups(tx, now, groupFull, dirty); e != nil {
		return e
	}
	generation := s.Generation() + 1
	_, e = tx.Exec("INSERT INTO usage_query_state VALUES('go_projection',?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", dataJSON(Row{"revision": revision, "prices": version, "generation": generation, "request_projection_version": 1}))
	if e != nil {
		return e
	}
	if _, e = tx.Exec("DELETE FROM usage_query_changes"); e != nil {
		return e
	}
	if e = tx.Commit(); e == nil {
		s.generation.Store(generation)
	}
	return e
}
func (s *Store) rebuildGroups(tx *sql.Tx, now time.Time, full bool, dirty map[string]bool) error {
	turns, e := queryJSON(tx, "SELECT data FROM usage_turns ORDER BY json_extract(data,'$.observed_at'),id")
	if e != nil {
		return e
	}
	links, e := queryJSON(tx, "SELECT data FROM usage_agent_links")
	if e != nil {
		return e
	}
	metadata := map[string]Row{}
	for _, incoming := range turns {
		if incoming["verified"] == false {
			continue
		}
		session, turn := dataString(incoming, "session_id"), dataString(incoming, "turn_id")
		if session == "" || turn == "" {
			continue
		}
		key := turnKey(session, turn)
		r := metadata[key]
		if r == nil {
			r = Row{}
		}
		for k, v := range incoming {
			if v != nil && v != "" {
				r[k] = v
			}
		}
		metadata[key] = r
	}
	resolve := func(key string) string {
		seen := map[string]bool{}
		original := key
		for {
			alias := dataString(metadata[key], "alias_of")
			if alias == "" {
				return key
			}
			if seen[key] {
				return original
			}
			seen[key] = true
			key = alias
		}
	}
	bases := map[string]*requestBase{}
	for key, meta := range metadata {
		if resolve(key) != key {
			continue
		}
		if dataString(meta, "prompt_preview") != "" || ValueBool(meta["has_usage"]) || ValueBool(meta["has_user_message"]) || dataString(meta, "continuation_of") != "" || dataString(meta, "root_turn_id") != "" || classifyRequest(meta) == "context_compaction" {
			bases[key] = &requestBase{meta: meta}
		}
	}
	// Index call ownership in SQLite; only per-turn IDs/metadata reside in the graph.
	rows, e := tx.Query("SELECT DISTINCT session_id,turn_id FROM usage_priced_calls WHERE turn_id<>''")
	if e != nil {
		return e
	}
	for rows.Next() {
		var session, turn string
		if e = rows.Scan(&session, &turn); e != nil {
			rows.Close()
			return e
		}
		key := resolve(turnKey(session, turn))
		if bases[key] == nil {
			meta := metadata[key]
			if meta == nil {
				meta = Row{"session_id": session, "turn_id": turn, "has_usage": true}
			}
			bases[key] = &requestBase{meta: meta}
		}
	}
	rows.Close()
	if e = s.rebuildSessionRoots(tx, metadata, links); e != nil {
		return e
	}
	edges := map[string]string{}
	byChild := map[string][]Row{}
	byParent := map[string][]Row{}
	for _, link := range links {
		child, parent := dataString(link, "child_session_id"), dataString(link, "parent_session_id")
		byChild[child] = append(byChild[child], link)
		byParent[parent] = append(byParent[parent], link)
	}
	for key, base := range bases {
		meta := base.meta
		ownershipKey := func(value string) string {
			if value != "" && !strings.HasPrefix(value, "turn:") {
				return turnKey(dataString(meta, "session_id"), value)
			}
			return value
		}
		if continuation := resolve(ownershipKey(dataString(meta, "continuation_of"))); continuation != "" && continuation != key && bases[continuation] != nil {
			edges[key] = continuation
			continue
		}
		if root := resolve(ownershipKey(dataString(meta, "root_turn_id"))); root != "" && root != key && bases[root] != nil && !ValueBool(meta["is_subagent"]) && !approvalRequest(meta) {
			edges[key] = root
			continue
		}
		session, parent := dataString(meta, "session_id"), dataString(meta, "parent_session_id")
		direct := byChild[session]
		if len(direct) > 0 || approvalRequest(meta) {
			meta["is_subagent"] = true
		}
		if !ValueBool(meta["is_subagent"]) {
			continue
		}
		candidates := map[string]bool{}
		if turn := dataString(meta, "parent_turn_id"); parent != "" && turn != "" {
			candidates[resolve(turnKey(parent, turn))] = true
		}
		// Guardian's inherited parent turn is structural evidence only when present
		// in the parent ledger. A parent thread by itself is deliberately insufficient.
		if approvalRequest(meta) && parent != "" {
			if turn := dataString(meta, "inherited_parent_turn_id"); turn != "" {
				candidate := resolve(turnKey(parent, turn))
				if bases[candidate] != nil {
					candidates[candidate] = true
				}
			}
		}
		for _, link := range append(append([]Row{}, direct...), byParent[parent]...) {
			exact := dataString(link, "child_session_id") == session
			path := parent != "" && dataString(meta, "agent_path") != "" && dataString(link, "target") == dataString(meta, "agent_path")
			if !exact && !path {
				continue
			}
			matches := false
			if childTurn := dataString(link, "child_turn_id"); childTurn != "" {
				matches = childTurn == dataString(meta, "turn_id")
			} else if dataString(link, "kind") == "spawn" {
				matches = ValueBool(meta["first_turn"])
			} else {
				for _, hash := range ValueStrings(meta["input_hashes"]) {
					if hash == dataString(link, "message_hash") {
						matches = true
					}
				}
			}
			if matches && dataString(link, "parent_turn_id") != "" {
				candidates[resolve(turnKey(dataString(link, "parent_session_id"), dataString(link, "parent_turn_id")))] = true
			}
		}
		if len(candidates) == 1 {
			for candidate := range candidates {
				if candidate != key && bases[candidate] != nil {
					edges[key] = candidate
				}
			}
		} else if len(candidates) > 1 {
			base.note = "存在多个父轮次，未合并费用"
		}
	}
	rootOf := func(key string) string {
		original := key
		seen := map[string]bool{}
		for edges[key] != "" {
			if seen[key] {
				return original
			}
			seen[key] = true
			key = edges[key]
		}
		return key
	}
	components := map[string][]string{}
	for key := range bases {
		root := rootOf(key)
		components[root] = append(components[root], key)
	}
	for root := range components {
		sort.Slice(components[root], func(i, j int) bool {
			a, b := components[root][i], components[root][j]
			if dataString(bases[a].meta, "started_at") == dataString(bases[b].meta, "started_at") {
				return a < b
			}
			return dataString(bases[a].meta, "started_at") < dataString(bases[b].meta, "started_at")
		})
	}
	affected := map[string]bool{}
	if !full {
		old, e := queryJSON(tx, "SELECT data FROM usage_query_turns")
		if e != nil {
			return e
		}
		for _, meta := range old {
			if dirty[dataString(meta, "session_id")] {
				affected[dataString(meta, "root_id")] = true
			}
		}
	}
	for root, keys := range components {
		for _, key := range keys {
			if full || dirty[dataString(bases[key].meta, "session_id")] {
				affected[root] = true
				break
			}
		}
	}
	if full {
		if _, e = tx.Exec("DELETE FROM usage_request_groups; DELETE FROM usage_request_members;"); e != nil {
			return e
		}
	} else {
		for root := range affected {
			if _, e = tx.Exec("DELETE FROM usage_request_groups WHERE id=?", root); e != nil {
				return e
			}
			if _, e = tx.Exec("DELETE FROM usage_request_members WHERE request_id=?", root); e != nil {
				return e
			}
		}
	}
	if _, e = tx.Exec("CREATE TEMP TABLE IF NOT EXISTS go_turns_seen(id TEXT PRIMARY KEY); DELETE FROM go_turns_seen;"); e != nil {
		return e
	}
	for key, meta := range metadata {
		meta["resolved_id"] = resolve(key)
		meta["root_id"] = rootOf(resolve(key))
		if _, e = tx.Exec("INSERT INTO usage_query_turns VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data", key, dataJSON(meta)); e != nil {
			return e
		}
		if _, e = tx.Exec("INSERT INTO go_turns_seen VALUES(?)", key); e != nil {
			return e
		}
	}
	if _, e = tx.Exec("DELETE FROM usage_query_turns WHERE id NOT IN(SELECT id FROM go_turns_seen)"); e != nil {
		return e
	}
	for root, keys := range components {
		if !affected[root] {
			continue
		}
		base := bases[root]
		group := CloneRow(base.meta)
		group["id"] = root
		group["root_id"] = root
		group["timestamp"] = firstString(group["started_at"], group["timestamp"])
		if timestamp := stamp(group["timestamp"]); timestamp != "" {
			group["timestamp"] = timestamp
		}
		group["record_kind"] = classifyRequest(group)
		if ValueBool(group["is_subagent"]) && dataString(group, "record_kind") == "user_request" {
			group["record_kind"] = "subagent_request"
		}
		group["association_note"] = base.note
		if ValueBool(group["is_subagent"]) && base.note == "" {
			group["association_note"] = "未能明确关联父请求，子代理单独计量"
		}
		summary := newMetricSum()
		models, sources, subagents, tiers := map[string]bool{}, map[string]bool{}, map[string]bool{}, map[string]bool{}
		composition := []Row{}
		callCount := 0
		ownReview, ownNormal := false, false
		hasHuman := false
		var earliest Row
		ownModel := dataString(group, "model")
		status := firstString(group["status"], "unknown")
		if status == "running" {
			if t, ok := ParseStamp(group["started_at"]); ok && now.Sub(t) > 24*time.Hour {
				status = "unknown"
			}
		}
		for _, key := range keys {
			meta := bases[key].meta
			if !ValueBool(meta["is_subagent"]) && !approvalRequest(meta) && ValueBool(meta["has_user_message"]) {
				hasHuman = true
			}
			composition = append(composition, Row{"id": key, "record_kind": firstString(meta["record_kind"], func() any {
				if ValueBool(meta["is_subagent"]) {
					return "subagent_request"
				}
				return "user_request"
			}()), "session_id": meta["session_id"], "turn_id": meta["turn_id"], "source": meta["source"], "parent_session_id": meta["parent_session_id"], "parent_turn_id": meta["parent_turn_id"]})
			if key != root && ValueBool(meta["is_subagent"]) {
				subagents[dataString(meta, "session_id")] = true
			}
			if key != root && dataString(meta, "status") == "running" {
				if t, ok := ParseStamp(meta["started_at"]); ok && now.Sub(t) < 24*time.Hour {
					status = "running"
				}
			}
			if key != root && dataString(meta, "status") == "unknown" && status == "completed" {
				status = "unknown"
			}
			// Alias membership includes both temporary and official turn identifiers.
			callRows, e := tx.Query(`SELECT data FROM usage_priced_calls WHERE session_id=? AND turn_id IN(SELECT ? UNION SELECT json_extract(t.data,'$.turn_id') FROM usage_query_turns t WHERE json_extract(t.data,'$.resolved_id')=?) ORDER BY timestamp,id`, dataString(meta, "session_id"), dataString(meta, "turn_id"), key)
			if e != nil {
				return e
			}
			for callRows.Next() {
				var raw string
				if e = callRows.Scan(&raw); e != nil {
					callRows.Close()
					return e
				}
				r := dataRow(raw)
				if key == root {
					if approvalRequest(r) {
						ownReview = true
					} else {
						ownNormal = true
					}
				}
				summary.add(r)
				models[dataString(r, "model")] = true
				tiers[normalizedTier(r["service_tier"])] = true
				for _, source := range ValueStrings(r["source_ids"]) {
					sources[source] = true
				}
				callCount++
				if key == root && (earliest == nil || dataString(r, "timestamp") < dataString(earliest, "timestamp")) {
					earliest = r
				}
			}
			if e = callRows.Err(); e != nil {
				callRows.Close()
				return e
			}
			callRows.Close()
			if _, e = tx.Exec(`INSERT OR IGNORE INTO usage_request_members SELECT ?,id FROM usage_priced_calls WHERE session_id=? AND turn_id IN(SELECT ? UNION SELECT json_extract(t.data,'$.turn_id') FROM usage_query_turns t WHERE json_extract(t.data,'$.resolved_id')=?)`, root, dataString(meta, "session_id"), dataString(meta, "turn_id"), key); e != nil {
				return e
			}
		}
		metrics := summary.row()
		for _, k := range []string{"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "cache_hit_rate", "unpriced_calls", "unpriced_tokens", "cost_complete"} {
			group[k] = metrics[k]
		}
		group["total_tokens"] = metrics["tokens"]
		group["cost_usd"] = metrics["usd"]
		group["pricing_status"] = "priced"
		if callCount == 0 {
			group["pricing_status"] = "unmetered"
		} else if metrics["usd"] == nil {
			group["pricing_status"] = "unpriced"
		} else if ValueInt(metrics["unpriced_calls"]) > 0 {
			group["pricing_status"] = "partial"
		}
		group["call_count"] = callCount
		if dataString(group, "record_kind") == "user_request" && !hasHuman {
			group["record_kind"] = "context_message"
		}
		if ownReview && !ownNormal && (!ValueBool(group["has_user_message"]) || ownModel == "" || ownModel == "unknown" || ownModel == "codex-auto-review") {
			group["record_kind"] = "automatic_approval_review"
			group["is_approval_review"] = true
			group["is_subagent"] = true
		}
		group["subagent_count"] = len(subagents)
		group["composition"] = composition
		if callCount == 0 {
			for _, source := range ValueStrings(group["source_ids"]) {
				sources[source] = true
			}
			if source := dataString(group, "source_id"); source != "" {
				sources[source] = true
			}
			if ownModel != "" && ownModel != "unknown" {
				models[ownModel] = true
			}
			if group["service_tier"] != nil {
				tiers[normalizedTier(group["service_tier"])] = true
			}
		}
		group["models"] = sortedKeys(models)
		group["source_ids"] = sortedKeys(sources)
		group["request_status"] = status
		group["status_label"] = map[string]string{"running": "回复中", "completed": "完成", "aborted": "已中断", "unknown": "未知"}[status]
		if earliest != nil {
			for _, k := range []string{"session_title", "prompt_preview", "output_preview", "reasoning_effort", "service_tier", "model_context_window"} {
				if group[k] == nil || group[k] == "" {
					group[k] = earliest[k]
				}
			}
			if dataString(group, "timestamp") == "" {
				group["timestamp"] = earliest["timestamp"]
			}
			if ownModel == "" {
				ownModel = dataString(earliest, "model")
			}
		}
		group["model"] = firstString(ownModel, "unknown")
		group["service_tiers"] = sortedKeys(tiers)
		if len(tiers) == 1 {
			group["service_tier"] = sortedKeys(tiers)[0]
		} else if len(tiers) > 1 {
			group["service_tier"] = "mixed"
		}
		var finalMeta Row
		duration := float64(0)
		durationKnown := true
		segments := 0
		for _, key := range keys {
			meta := bases[key].meta
			mainContinuation := !ValueBool(meta["is_subagent"]) && !approvalRequest(meta) && (dataString(meta, "continuation_of") != "" || dataString(meta, "root_turn_id") != "" && key != root)
			if key == root || mainContinuation {
				segments++
				if d, ok := ValueFloat(meta["duration_ms"]); ok && d >= 0 {
					duration += d
				} else {
					start, ok := ParseStamp(meta["started_at"])
					end, ok2 := ParseStamp(meta["ended_at"])
					if ok && ok2 && !end.Before(start) {
						duration += float64(end.Sub(start).Milliseconds())
					} else {
						durationKnown = false
					}
				}
			}
			if mainContinuation && (finalMeta == nil || dataString(meta, "ended_at") > dataString(finalMeta, "ended_at")) {
				finalMeta = meta
			}
		}
		if finalMeta != nil && dataString(finalMeta, "output_preview") != "" {
			group["output_preview"] = finalMeta["output_preview"]
		}
		if finalMeta != nil && status != "running" {
			status = firstString(finalMeta["status"], status)
			group["ended_at"] = finalMeta["ended_at"]
		}
		group["status"], group["request_status"] = status, status
		group["status_label"] = map[string]string{"running": "回复中", "completed": "完成", "aborted": "已中断", "unknown": "未知"}[status]
		if segments > 1 {
			group["duration_segments"] = segments
			group["duration_ms"] = nil
			if durationKnown {
				group["duration_ms"] = duration
			}
		}
		if title := func() string {
			var title string
			_ = tx.QueryRow("SELECT title FROM usage_session_titles WHERE session_id=?", group["session_id"]).Scan(&title)
			return title
		}(); title != "" {
			group["session_title"] = title
		}
		if group["duration_ms"] == nil && segments <= 1 {
			start, ok := ParseStamp(group["started_at"])
			end, ok2 := ParseStamp(group["ended_at"])
			if ok && ok2 && !end.Before(start) {
				group["duration_ms"] = float64(end.Sub(start).Milliseconds())
			}
		}
		group["duration_running"] = status == "running"
		group["duration_started_at"] = group["started_at"]
		if e = saveGroup(tx, group); e != nil {
			return e
		}
	}
	// Rows without ownership remain individual calls, never synthetic user requests.
	if _, e = tx.Exec("DELETE FROM usage_request_groups WHERE record_kind='unassigned' AND id NOT IN(SELECT 'unassigned:'||id FROM usage_priced_calls WHERE turn_id=''); DELETE FROM usage_request_members WHERE request_id LIKE 'unassigned:%' AND request_id NOT IN(SELECT id FROM usage_request_groups)"); e != nil {
		return e
	}
	last := ""
	for {
		query := "SELECT data FROM usage_priced_calls WHERE turn_id='' AND id>?"
		if !full {
			query += " AND id IN(SELECT item_id FROM usage_query_changes WHERE kind='record')"
		}
		query += " ORDER BY id LIMIT 500"
		records, e := queryJSON(tx, query, last)
		if e != nil {
			return e
		}
		if len(records) == 0 {
			break
		}
		for _, r := range records {
			id := dataString(r, "id")
			last = id
			r["id"] = "unassigned:" + id
			r["record_kind"] = "unassigned"
			r["call_count"] = 1
			if e = saveGroup(tx, r); e != nil {
				return e
			}
			if _, e = tx.Exec("INSERT OR IGNORE INTO usage_request_members VALUES(?,?)", r["id"], id); e != nil {
				return e
			}
		}
		if len(records) < 500 {
			break
		}
	}
	return nil
}
func saveGroup(tx *sql.Tx, r Row) error {
	_, e := tx.Exec("INSERT INTO usage_request_groups VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,record_kind=excluded.record_kind,is_subagent=excluded.is_subagent,subagent_count=excluded.subagent_count,data=excluded.data WHERE data<>excluded.data", r["id"], dataString(r, "timestamp"), dataString(r, "record_kind"), boolInt(ValueBool(r["is_subagent"])), ValueInt(r["subagent_count"]), dataJSON(r))
	return e
}
func sortedKeys(m map[string]bool) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		if k != "" {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	return keys
}
func metricRow(r Row) Row { a := newMetricSum(); a.add(r); return a.row() }

type metricSum struct {
	totals                   map[string]int64
	valid                    map[string]int
	skipped                  map[string]int
	records, unpriced        int
	unpricedTokens           int64
	cost, compensation       float64
	cacheInput, cacheRead    int64
	cacheValid, cacheSkipped int
}

func newMetricSum() *metricSum {
	return &metricSum{totals: map[string]int64{}, valid: map[string]int{}, skipped: map[string]int{}}
}
func (m *metricSum) add(r Row) {
	m.records++
	total, tok := knownCount(r["total_tokens"])
	if _, exists := r["input_tokens"]; exists {
		in, ok := knownCount(r["input_tokens"])
		out, ok2 := knownCount(r["output_tokens"])
		tok = tok && ok && ok2 && total == in+out
	}
	tok = tok && !strings.HasPrefix(dataString(r, "quality"), "invalid")
	if tok {
		m.totals["tokens"] += total
		m.valid["tokens"]++
	} else {
		m.skipped["tokens"]++
	}
	cost, priced := ValueFloat(r["cost_usd"])
	priced = priced && cost >= 0 && (dataString(r, "pricing_status") == "priced" || dataString(r, "pricing_status") == "estimated" || dataString(r, "pricing_status") == "")
	if priced {
		y := cost - m.compensation
		t := m.cost + y
		m.compensation = (t - m.cost) - y
		m.cost = t
		m.valid["usd"]++
	} else {
		m.unpriced++
		m.skipped["usd"]++
		if tok {
			m.unpricedTokens += total
		}
	}
	quality := strings.Split(dataString(r, "quality"), ":")[0]
	if quality == "" || quality == "response" || quality == "legacy_last" || quality == "cumulative_delta" && strings.HasPrefix(dataString(r, "id"), "response:") {
		m.totals["requests"]++
		m.valid["requests"]++
	} else {
		m.skipped["requests"]++
	}
	for _, k := range tokenFields[:5] {
		if n, ok := knownCount(r[k]); ok {
			m.totals[k] += n
			m.valid[k]++
		} else {
			m.skipped[k]++
		}
	}
	in, ok := knownCount(r["input_tokens"])
	cache, ok2 := knownCount(r["cached_input_tokens"])
	if ok && ok2 && cache <= in && !strings.HasPrefix(dataString(r, "quality"), "invalid") {
		m.cacheInput += in
		m.cacheRead += cache
		m.cacheValid++
	} else {
		m.cacheSkipped++
	}
}
func (m *metricSum) row() Row {
	r := Row{"records": m.records, "valid_counts": m.valid, "skipped": m.skipped, "unpriced_calls": m.unpriced, "unpriced_tokens": m.unpricedTokens, "cost_complete": m.unpriced == 0, "cache_input_tokens": m.cacheInput, "cache_read_tokens": m.cacheRead, "cache_valid_records": m.cacheValid, "cache_skipped_records": m.cacheSkipped, "cache_hit_rate": nil, "usd": nil}
	for _, k := range append([]string{"tokens", "requests"}, tokenFields[:5]...) {
		r[k] = nil
		if m.valid[k] > 0 {
			r[k] = m.totals[k]
		}
	}
	if m.valid["usd"] > 0 {
		r["usd"] = m.cost
	}
	if m.cacheInput > 0 {
		r["cache_hit_rate"] = float64(m.cacheRead) / float64(m.cacheInput)
	}
	return r
}
