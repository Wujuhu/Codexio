package backend

import "database/sql"

// Live, explicit transcript IDs only. The bounded hints also cover a packet
// arriving just before the unchanged incremental collector sees its log row.
func (s *Store) ObserveClaudeContexts(sample Row) {
	if s.options.Mock {
		return
	}
	s.mu.Lock()
	if s.claudeContexts == nil {
		s.claudeContexts = map[string]Row{}
		s.claudeContextDirty = map[string]bool{}
	}
	changed := false
	for _, point := range ValueRows(sample["contexts"]) {
		session, uuid, prompt := dataString(point, "session_id"), dataString(point, "message_uuid"), dataString(point, "prompt_id")
		at, known := ValueFloat(point["observed_at"])
		if session == "" || uuid == "" && prompt == "" || len(claudePointFields(point)) == 0 || !known {
			continue
		}
		key := session + "|" + uuid + "|" + prompt
		old := s.claudeContexts[key]
		if len(old) > 0 {
			oldAt, _ := ValueFloat(old["observed_at"])
			if oldAt > at {
				continue
			}
		}
		merged := CloneRow(old)
		if merged == nil {
			merged = Row{}
		}
		for k, v := range point {
			merged[k] = v
		}
		if dataJSON(claudePointFields(merged)) == dataJSON(claudePointFields(old)) {
			continue
		}
		s.claudeContexts[key] = merged
		s.claudeContextDirty[key] = true
		changed = true
	}
	for len(s.claudeContexts) > 256 {
		oldest := ""
		var at float64
		for key, point := range s.claudeContexts {
			v, _ := ValueFloat(point["observed_at"])
			if oldest == "" || v < at {
				oldest = key
				at = v
			}
		}
		delete(s.claudeContexts, oldest)
		delete(s.claudeContextDirty, oldest)
	}
	s.mu.Unlock()
	if changed {
		s.Refresh()
	}
}

// Reuse the ledger's model/effort/context fields; these bounded observations
// are keyed by explicit source IDs, never the current global configuration.
func claudePointFields(point Row) Row {
	fields := Row{}
	if window, ok := knownCount(point["window"]); ok && window > 0 {
		fields["model_context_window"] = window
	}
	for _, key := range []string{"model", "reasoning_effort"} {
		if value := dataString(point, key); value != "" {
			fields[key] = value
		}
	}
	return fields
}
func claudeContextFields(tx *sql.Tx, session, uuid, prompt string) Row {
	if session == "" || uuid == "" && prompt == "" {
		return Row{}
	}
	var window int64
	var model, effort string
	e := tx.QueryRow("SELECT window,model,reasoning_effort FROM claude_context_windows WHERE session_id=? AND ((message_uuid<>'' AND message_uuid=?) OR (prompt_id<>'' AND prompt_id=?)) ORDER BY observed_at DESC LIMIT 1", session, uuid, prompt).Scan(&window, &model, &effort)
	if e != nil {
		return Row{}
	}
	return claudePointFields(Row{"window": window, "model": model, "reasoning_effort": effort})
}
func claudeContextWindow(tx *sql.Tx, session, uuid, prompt string) any {
	return claudeContextFields(tx, session, uuid, prompt)["model_context_window"]
}
func (s *Store) applyClaudeContexts() error {
	s.mu.RLock()
	pending := map[string]Row{}
	for key := range s.claudeContextDirty {
		pending[key] = CloneRow(s.claudeContexts[key])
	}
	s.mu.RUnlock()
	if len(pending) == 0 {
		return nil
	}
	if !s.claudeContextIndexReady {
		if _, e := s.db.Exec("CREATE INDEX IF NOT EXISTS claude_context_nodes ON usage_records(json_extract(data,'$.node_id')) WHERE json_extract(data,'$.client_kind')='claude_code'"); e != nil {
			return e
		}
		s.claudeContextIndexReady = true
	}
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	ns := claudeNamespace(claudeRoot())
	for _, point := range pending {
		session := ns + "session:" + dataString(point, "session_id")
		uuid, prompt := dataString(point, "message_uuid"), dataString(point, "prompt_id")
		fields := claudeContextFields(tx, session, uuid, prompt)
		var oldAt float64
		_ = tx.QueryRow("SELECT observed_at FROM claude_context_windows WHERE session_id=? AND message_uuid=? AND prompt_id=?", session, uuid, prompt).Scan(&oldAt)
		at, _ := ValueFloat(point["observed_at"])
		if oldAt > at {
			continue
		}
		for key, value := range claudePointFields(point) {
			fields[key] = value
		}
		if _, e = tx.Exec("INSERT INTO claude_context_windows(session_id,message_uuid,prompt_id,observed_at,window,model,reasoning_effort) VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_id,message_uuid,prompt_id) DO UPDATE SET observed_at=excluded.observed_at,window=excluded.window,model=excluded.model,reasoning_effort=excluded.reasoning_effort", session, uuid, prompt, at, ValueInt(fields["model_context_window"]), dataString(fields, "model"), dataString(fields, "reasoning_effort")); e != nil {
			return e
		}
		if uuid != "" {
			callFields := CloneRow(fields)
			if model := dataString(fields, "model"); model != "" {
				callFields["requested_model"] = model
			}
			patch := dataJSON(callFields)
			if _, e = tx.Exec("UPDATE usage_records SET model=coalesce(nullif(json_extract(?, '$.model'),''),model),data=json_patch(data,?) WHERE session_id=? AND json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.node_id')=? AND data<>json_patch(data,?)", patch, patch, session, ns+"uuid:"+uuid, patch); e != nil {
				return e
			}
		}
		patch := dataJSON(fields)
		if _, e = tx.Exec("UPDATE claude_requests SET data=json_patch(data,?) WHERE session_id=? AND ((?<>'' AND json_extract(data,'$.user_uuid')=?) OR (?<>'' AND id=?)) AND data<>json_patch(data,?)", patch, session, uuid, uuid, prompt, ns+"request:"+prompt, patch); e != nil {
			return e
		}
	}
	if _, e = tx.Exec("DELETE FROM claude_context_windows WHERE rowid NOT IN(SELECT rowid FROM claude_context_windows ORDER BY observed_at DESC LIMIT 256)"); e != nil {
		return e
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	s.mu.Lock()
	for key, point := range pending {
		if dataJSON(s.claudeContexts[key]) == dataJSON(point) {
			delete(s.claudeContextDirty, key)
		}
	}
	s.mu.Unlock()
	return nil
}
