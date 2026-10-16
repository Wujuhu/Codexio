package backend

import (
	"context"
	"database/sql"
	"strings"
)

var claudeUsageFields = append(append([]string{}, tokenFields...), "raw_input_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens")

func mergeClaudeUsage(old, incoming Row) Row {
	r := CloneRow(incoming)
	oldFinalFacts, newFinalFacts := claudeTerminalFacts(old), claudeTerminalFacts(incoming)
	r["terminal_usage_values"] = newFinalFacts
	if len(old) == 0 {
		return r
	}
	oldComplete, newComplete := ValueBool(old["usage_complete"]), ValueBool(incoming["usage_complete"])
	oldTerminal, newTerminal := ValueBool(old["usage_terminal"]), ValueBool(incoming["usage_terminal"])
	oldZero, newZero := ValueBool(old["usage_zero"]), ValueBool(incoming["usage_zero"])
	same := true
	oldPositive := false
	for _, field := range claudeUsageFields {
		prior, priorKnown := knownCount(oldFinalFacts[field])
		next, nextKnown := knownCount(newFinalFacts[field])
		if priorKnown && nextKnown && prior != next {
			same = false
		}
		if n, ok := knownCount(old[field]); ok && n > 0 {
			oldPositive = true
		}
	}
	conflict := ValueBool(old["usage_conflict"]) || ValueBool(incoming["usage_conflict"])
	// Known provisional zero is allowed to acquire its later final value. Two
	// differing nonzero complete terminal observations stay durably unresolved.
	terminalPositive := false
	for _, value := range oldFinalFacts {
		if n, ok := knownCount(value); ok && n > 0 {
			terminalPositive = true
		}
	}
	if oldTerminal && newTerminal && !same && terminalPositive {
		conflict = true
	}
	replace := newComplete && ((!oldComplete && (!oldTerminal || newTerminal)) || newTerminal && !oldTerminal || oldZero && !newZero && (!oldTerminal || newTerminal))
	if newZero && oldPositive {
		replace = false
	}
	if !replace {
		for _, field := range claudeUsageFields {
			if _, ok := knownCount(old[field]); ok {
				if _, incomingKnown := knownCount(incoming[field]); !incomingKnown || !newTerminal || oldTerminal {
					r[field] = old[field]
				}
			} // retain facts; never max components
		}
		r["input_complete"] = old["input_complete"]
		r["usage_complete"] = oldComplete
		r["usage_zero"] = old["usage_zero"]
		if oldTerminal {
			r["final_missing_fields"] = old["final_missing_fields"]
		}
		// Terminal fragments do not assert missing final components. Known original
		// facts remain durable while the public complete-input/total stays missing.
		if newTerminal && !newComplete && !oldTerminal {
			r["usage_complete"] = false
			if incoming["input_complete"] == false {
				r["input_complete"] = false
			}
		}
	}
	for _, field := range claudeUsageFields {
		if _, known := knownCount(r[field]); !known {
			if _, oldKnown := knownCount(old[field]); oldKnown {
				r[field] = old[field]
			}
		}
	}
	facts := CloneRow(oldFinalFacts)
	if replace && newTerminal && !conflict {
		// Terminal provenance follows the legitimately selected complete observation,
		// including when an earlier incomplete terminal had only known zeros.
		facts = CloneRow(newFinalFacts)
		for field, value := range oldFinalFacts {
			if _, known := knownCount(facts[field]); !known {
				facts[field] = value
			}
		}
	} else {
		for field, value := range newFinalFacts {
			if _, known := knownCount(facts[field]); !known {
				facts[field] = value
			}
		}
	}
	r["terminal_usage_values"] = facts
	r["call_timing"] = mergeClaudeTiming(ValueRow(old["call_timing"]), ValueRow(incoming["call_timing"]))
	r["call_timing_checked"] = ValueBool(old["call_timing_checked"]) && ValueBool(incoming["call_timing_checked"]) && !ValueBool(old["call_timing_incomplete"])
	r["call_timing_incomplete"] = ValueBool(old["call_timing_incomplete"]) || ValueBool(incoming["call_timing_incomplete"])
	r["usage_terminal"] = oldTerminal || newTerminal
	r["usage_conflict"] = conflict
	for _, field := range []string{"timestamp", "session_id", "claude_session_scope", "claude_agent_id"} {
		if old[field] != nil && old[field] != "" {
			r[field] = old[field]
		}
	}
	if dataString(r, "claude_request_id") == "" {
		r["claude_request_id"] = old["claude_request_id"]
	}
	for _, field := range []string{"prompt_preview", "output_preview", "model", "returned_model", "upstream_model", "requested_model", "reasoning_effort", "service_tier", "auth_mode", "billing_source"} {
		if r[field] == nil || r[field] == "" {
			r[field] = old[field]
		}
	}
	if ValueBool(r["usage_terminal"]) {
		r["request_status"] = "completed"
	}
	return r
}
func claudeReplyID(ns, owner, session, agent, response string) string {
	scope := owner
	if scope == "" {
		scope = session
	}
	return ns + "response:" + HashString(scope+":"+agent+"|"+response)
}
func (s *Store) reconcileClaudeReplies(tx *sql.Tx, ns string) error {
	last := ""
	for {
		records, e := queryJSON(tx, `SELECT data FROM usage_records WHERE json_extract(data,'$.client_kind')='claude_code' AND instr(id,?)=1 AND id>? AND id IN(SELECT item_id FROM usage_query_changes WHERE kind='record') ORDER BY id LIMIT 250`, ns, last)
		if e != nil {
			return e
		}
		if len(records) == 0 {
			break
		}
		for _, r := range records {
			oldID := dataString(r, "id")
			last = oldID
			owner := dataString(r, "claude_request_id")
			agent := dataString(r, "claude_agent_id")
			if agent == "" {
				if at := strings.Index(dataString(r, "session_id"), ":agent:"); at >= 0 {
					agent = dataString(r, "session_id")[at+7:]
				}
			}
			canonical := claudeReplyID(ns, owner, dataString(r, "session_id"), agent, dataString(r, "response_id"))
			if canonical == oldID {
				continue
			}
			r["id"] = canonical
			r["claude_agent_id"] = agent
			if e = s.saveClaudeRecord(tx, r); e != nil {
				return e
			}
			if _, e = tx.Exec(`INSERT OR IGNORE INTO claude_locators SELECT ?,uuid,path,offset,identity,line_hash,available,generation FROM claude_locators WHERE item_id=?`, canonical, oldID); e != nil {
				return e
			}
			if _, e = tx.Exec(`INSERT OR IGNORE INTO usage_record_sources SELECT ?,source_id FROM usage_record_sources WHERE record_id=?`, canonical, oldID); e != nil {
				return e
			}
			if _, e = tx.Exec(`UPDATE claude_nodes SET data=json_set(data,'$.record_id',?) WHERE json_extract(data,'$.record_id')=?`, canonical, oldID); e != nil {
				return e
			}
			if _, e = tx.Exec(`INSERT INTO usage_query_changes(kind,item_id) VALUES('title',?),('claude_request',?)`, r["session_id"], owner); e != nil {
				return e
			}
			for _, statement := range []string{"DELETE FROM usage_records WHERE id=?", "DELETE FROM usage_record_sources WHERE record_id=?", "DELETE FROM claude_locators WHERE item_id=?", "DELETE FROM usage_request_members WHERE record_id=?"} {
				if _, e = tx.Exec(statement, oldID); e != nil {
					return e
				}
			}
		}
		if len(records) < 250 {
			break
		}
	}
	return nil
}
func (s *Store) applyClaudeEvents(tx *sql.Tx, ns string) error {
	last := ""
	for {
		nodes, e := queryJSON(tx, `SELECT data FROM claude_nodes WHERE instr(id,?)=1 AND id>? AND request_id<>'' AND coalesce(json_extract(data,'$.is_subagent'),0)=0 AND (json_extract(data,'$.synthetic_error')=1 OR json_extract(data,'$.subtype')='turn_duration') AND coalesce(json_extract(data,'$.event_applied_owner'),'')<>request_id ORDER BY id LIMIT 250`, ns, last)
		if e != nil {
			return e
		}
		if len(nodes) == 0 {
			break
		}
		for _, node := range nodes {
			last = dataString(node, "id")
			owner := dataString(node, "request_id")
			r := claudeGetRequest(tx, owner)
			if len(r) == 0 || dataString(r, "session_id") != dataString(node, "session_id") {
				continue
			}
			if ValueBool(node["synthetic_error"]) {
				r["request_status"] = "failed"
				r["status"] = "failed"
				r["ended_at"] = node["timestamp"]
			}
			if dataString(node, "subtype") == "turn_duration" {
				if n, ok := ValueFloat(node["duration_ms"]); ok && n >= 0 && dataString(node, "timestamp") >= dataString(r, "duration_observed_at") {
					r["duration_ms"] = n
					r["explicit_duration_ms"] = n
					r["explicit_ended_at"] = node["timestamp"]
					r["duration_source"] = "turn_duration"
					r["duration_observed_at"] = node["timestamp"]
					if dataString(r, "request_status") != "failed" && dataString(r, "request_status") != "aborted" {
						r["request_status"] = "completed"
						r["status"] = "completed"
						r["ended_at"] = node["timestamp"]
					}
				}
			}
			if e = claudeSaveRequest(tx, r); e != nil {
				return e
			}
			if _, e = tx.Exec(`INSERT OR IGNORE INTO claude_locators SELECT ?,uuid,path,offset,identity,line_hash,available,generation FROM claude_locators WHERE item_id=?`, owner, node["id"]); e != nil {
				return e
			}
			node["event_applied_owner"] = owner
			if _, e = tx.Exec("UPDATE claude_nodes SET data=? WHERE id=?", dataJSON(node), node["id"]); e != nil {
				return e
			}
		}
		if len(nodes) < 250 {
			break
		}
	}
	return nil
}
func (s *Store) invalidateClaudePath(path string, deleted bool, prefix bool) error {
	predicate := "path=?"
	if prefix {
		predicate = "instr(path,?)=1"
	}
	available := -1
	if deleted {
		available = 0
	}
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	result, e := tx.Exec("UPDATE claude_locators SET available=? WHERE "+predicate+" AND available<>?", available, path, available)
	if e != nil {
		return e
	}
	cursorPredicate := strings.ReplaceAll(predicate, "path", "json_extract(data,'$.path')")
	if _, e = tx.Exec("UPDATE usage_cursors SET data=json_set(data,'$.source_unavailable',json('true')) WHERE "+cursorPredicate, path); e != nil {
		return e
	}
	if deleted {
		if _, e = tx.Exec("UPDATE usage_cursors SET data=json_set(data,'$.search_version',0) WHERE "+cursorPredicate, path); e != nil {
			return e
		}
		if _, e = tx.Exec("DELETE FROM claude_search WHERE "+predicate, path); e != nil {
			return e
		}
	}
	n, _ := result.RowsAffected()
	if e = tx.Commit(); e != nil {
		return e
	}
	if n > 0 {
		s.generation.Add(1)
	}
	return nil
}
func (s *Store) restoreClaudeLocators(ctx context.Context, path, identity, generation string, searchPending bool) (bool, error) {
	if _, err := s.db.Exec(`UPDATE usage_records SET data=json_set(data,'$.call_timing_checked',json('false')) WHERE json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.call_timing_incomplete')=1 AND id IN(SELECT item_id FROM claude_locators WHERE path=? AND available<>1 AND identity=? AND generation=?)`, path, identity, generation); err != nil {
		return false, err
	}

	result, e := s.db.Exec("UPDATE claude_locators SET available=1 WHERE path=? AND identity=? AND generation=? AND available<>1", path, identity, generation)
	if e != nil {
		return false, e
	}
	n, _ := result.RowsAffected()
	if n > 0 {
		s.generation.Add(1)
	}
	if !searchPending {
		return true, nil
	}
	// A confirmed deletion removes body search text. On return, recover only
	// indexed locations in small batches; do not re-ingest usage or transcript history.
	rows, e := s.db.Query(`SELECT DISTINCT l.uuid,l.path,l.offset,l.identity,l.line_hash FROM claude_locators l LEFT JOIN claude_search b ON b.uuid=l.uuid AND b.path=l.path WHERE l.path=? AND l.available=1 AND b.uuid IS NULL LIMIT 64`, path)
	if e != nil {
		return false, e
	}
	locators := []Row{}
	for rows.Next() {
		var uuid, path, identity, hash string
		var offset int64
		if e = rows.Scan(&uuid, &path, &offset, &identity, &hash); e != nil {
			rows.Close()
			return false, e
		}
		locators = append(locators, Row{"uuid": uuid, "path": path, "offset": offset, "identity": identity, "line_hash": hash})
	}
	rows.Close()
	complete := len(locators) < 64
	for _, locator := range locators {
		if e = ctx.Err(); e != nil {
			return false, e
		}
		entry, err := s.claudeReadLocator(locator)
		if err != nil {
			complete = false
			continue
		}
		tx, err := s.db.Begin()
		if err != nil {
			return false, err
		}
		err = claudeSaveSearch(tx, entry, path, generation)
		if err == nil {
			err = tx.Commit()
		} else {
			tx.Rollback()
		}
		if err != nil {
			return false, err
		}
	}
	if len(locators) > 0 {
		s.generation.Add(1)
	}
	return complete, nil
}
func claudeSaveSearch(tx *sql.Tx, entry Row, path, generation string) error {
	text := ""
	if claudeHuman(entry) || dataString(entry, "type") == "assistant" {
		text, _ = claudeText(ValueRow(entry["message"])["content"])
	}
	_, e := tx.Exec(`INSERT INTO claude_search(uuid,path,text,generation) VALUES(?,?,?,?) ON CONFLICT(uuid,path) DO UPDATE SET text=excluded.text,generation=excluded.generation WHERE text<>excluded.text OR generation<>excluded.generation`, entry["uuid"], path, text, generation)
	return e
}
func (s *Store) claudeIncompleteCount() (int, error) {
	var revision int64
	if e := s.db.QueryRow("SELECT revision FROM claude_revisions WHERE kind='calls'").Scan(&revision); e != nil {
		return 0, e
	}
	if revision == s.claudeCountRevision {
		return s.claudeIncomplete, nil
	}
	var n int
	e := s.db.QueryRow(`SELECT count(*) FROM usage_records WHERE json_extract(data,'$.client_kind')='claude_code' AND (coalesce(json_extract(data,'$.usage_complete'),0)=0 OR json_extract(data,'$.usage_conflict')=1)`).Scan(&n)
	if e == nil {
		s.claudeCountRevision = revision
		s.claudeIncomplete = n
	}
	return n, e
}

func (s *Store) verifyClaudeOwnerSessions(tx *sql.Tx, ns string) error {
	last := ""
	for {
		nodes, e := queryJSON(tx, `SELECT data FROM claude_nodes WHERE instr(id,?)=1 AND request_id<>'' AND coalesce(json_extract(data,'$.owner_session_verified'),0)=0 AND id>? ORDER BY id LIMIT 250`, ns, last)
		if e != nil {
			return e
		}
		if len(nodes) == 0 {
			break
		}
		for _, node := range nodes {
			last = dataString(node, "id")
			owner := claudeGetRequest(tx, dataString(node, "request_id"))
			if len(owner) == 0 {
				continue
			}
			canonical := dataString(owner, "session_id")
			agent := dataString(node, "claude_agent_id")
			if agent == "" {
				if at := strings.Index(dataString(node, "session_id"), ":agent:"); at >= 0 {
					agent = dataString(node, "session_id")[at+7:]
				}
			}
			if ValueBool(node["is_subagent"]) && agent != "" {
				canonical += ":agent:" + agent
			}
			node["session_id"] = canonical
			node["claude_agent_id"] = agent
			node["owner_session_verified"] = true
			if _, e = tx.Exec("UPDATE claude_nodes SET session_id=?,data=? WHERE id=?", canonical, dataJSON(node), node["id"]); e != nil {
				return e
			}
		}
		if len(nodes) < 250 {
			break
		}
	}
	return nil
}

func claudeTerminalFacts(r Row) Row {
	if value, exists := r["terminal_usage_values"]; exists {
		return CloneRow(ValueRow(value))
	}
	result := Row{}
	if !ValueBool(r["usage_terminal"]) {
		return result
	}
	missing := map[string]bool{}
	for _, field := range ValueStrings(r["final_missing_fields"]) {
		missing[field] = true
	}
	for _, field := range claudeUsageFields {
		if missing[field] || field == "reasoning_output_tokens" {
			continue
		}
		if field == "input_tokens" && (r["input_complete"] == false || missing["raw_input_tokens"] || missing["cached_input_tokens"] || missing["cache_write_input_tokens"]) {
			continue
		}
		if field == "total_tokens" && !ValueBool(r["usage_complete"]) {
			continue
		}
		if _, known := knownCount(r[field]); known {
			result[field] = r[field]
		}
	}
	return result
}
