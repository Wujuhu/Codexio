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
		window, ok := knownCount(point["window"])
		at, known := ValueFloat(point["observed_at"])
		if session == "" || uuid == "" && prompt == "" || !ok || window <= 0 || !known {
			continue
		}
		key := session + "|" + uuid + "|" + prompt
		old := s.claudeContexts[key]
		if len(old) > 0 {
			oldAt, _ := ValueFloat(old["observed_at"])
			if oldAt > at || ValueInt(old["window"]) == window {
				continue
			}
		}
		s.claudeContexts[key] = CloneRow(point)
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
func claudeContextWindow(tx *sql.Tx, session, uuid, prompt string) any {
	if session == "" || uuid == "" && prompt == "" {
		return nil
	}
	var window int64
	e := tx.QueryRow("SELECT window FROM claude_context_windows WHERE session_id=? AND ((message_uuid<>'' AND message_uuid=?) OR (prompt_id<>'' AND prompt_id=?)) ORDER BY observed_at DESC LIMIT 1", session, uuid, prompt).Scan(&window)
	if e == nil && window > 0 {
		return window
	}
	return nil
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
		window := point["window"]
		if _, e = tx.Exec("INSERT INTO claude_context_windows VALUES(?,?,?,?,?) ON CONFLICT(session_id,message_uuid,prompt_id) DO UPDATE SET observed_at=excluded.observed_at,window=excluded.window WHERE excluded.observed_at>=observed_at AND excluded.window<>window", session, uuid, prompt, point["observed_at"], window); e != nil {
			return e
		}
		if uuid != "" {
			if _, e = tx.Exec("UPDATE usage_records SET data=json_set(data,'$.model_context_window',?) WHERE session_id=? AND json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.node_id')=? AND coalesce(json_extract(data,'$.model_context_window'),0)<>?", window, session, ns+"uuid:"+uuid, window); e != nil {
				return e
			}
		}
		if _, e = tx.Exec("UPDATE claude_requests SET data=json_set(data,'$.model_context_window',?) WHERE session_id=? AND ((?<>'' AND json_extract(data,'$.user_uuid')=?) OR (?<>'' AND id=?)) AND coalesce(json_extract(data,'$.model_context_window'),0)<>?", window, session, uuid, uuid, prompt, ns+"request:"+prompt, window); e != nil {
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
