package backend

import (
	"database/sql"
	"strings"
)

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
		if id := dataString(merged, "model_display_id"); id != "" && requestModelID(id) != requestModelID(dataString(merged, "model")) {
			delete(merged, "model_display_name")
			delete(merged, "model_display_id")
			delete(merged, "model_display_source")
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
	for _, key := range []string{"model", "reasoning_effort", "model_display_name", "model_display_id", "model_display_source"} {
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
	rows, e := tx.Query("SELECT window,model,reasoning_effort,model_display_name,model_display_id,model_display_source FROM claude_context_windows WHERE session_id=? AND ((message_uuid<>'' AND message_uuid=?) OR (prompt_id<>'' AND prompt_id=?)) ORDER BY observed_at DESC", session, uuid, prompt)
	if e != nil {
		return Row{}
	}
	defer rows.Close()
	fields := Row{}
	for rows.Next() {
		var window int64
		var model, effort, name, displayID, displaySource string
		if e = rows.Scan(&window, &model, &effort, &name, &displayID, &displaySource); e != nil {
			break
		}
		if len(fields) == 0 {
			fields = claudePointFields(Row{"window": window, "model": model, "reasoning_effort": effort})
		}
		if id := requestModelID(firstString(displayID, model)); name != "" && id != "" && id == requestModelID(dataString(fields, "model")) {
			fields["model_display_name"], fields["model_display_id"], fields["model_display_source"] = name, displayID, displaySource
			break
		}
	}
	return fields
}
func claudeContextWindow(tx *sql.Tx, session, uuid, prompt string) any {
	return claudeContextFields(tx, session, uuid, prompt)["model_context_window"]
}

// Once a request has a display name, keep its model and name together while
// still allowing later samples to supply context capacity and effort.
func claudeContextPatch(fields Row) (sql.NamedArg, sql.NamedArg) {
	contextFields := CloneRow(fields)
	for _, key := range []string{"model", "requested_model", "model_display_name", "model_display_id", "model_display_source"} {
		delete(contextFields, key)
	}
	return sql.Named("full", dataJSON(fields)), sql.Named("context", dataJSON(contextFields))
}

const claudeContextMerge = "CASE WHEN coalesce(json_extract(data,'$.model_display_name'),'')='' THEN @full ELSE @context END"

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
			if strings.HasPrefix(key, "model_display_") {
				continue
			}
			fields[key] = value
		}
		model := requestModelID(dataString(fields, "model"))
		if model != requestModelID(dataString(fields, "model_display_id")) {
			delete(fields, "model_display_name")
			delete(fields, "model_display_id")
			delete(fields, "model_display_source")
		}
		if model != "" && dataString(fields, "model_display_name") == "" {
			copyRequestModelName(fields, point)
		}
		if _, e = tx.Exec("INSERT INTO claude_context_windows(session_id,message_uuid,prompt_id,observed_at,window,model,reasoning_effort,model_display_name,model_display_id,model_display_source) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(session_id,message_uuid,prompt_id) DO UPDATE SET observed_at=excluded.observed_at,window=excluded.window,model=excluded.model,reasoning_effort=excluded.reasoning_effort,model_display_name=excluded.model_display_name,model_display_id=excluded.model_display_id,model_display_source=excluded.model_display_source", session, uuid, prompt, at, ValueInt(fields["model_context_window"]), dataString(fields, "model"), dataString(fields, "reasoning_effort"), dataString(fields, "model_display_name"), dataString(fields, "model_display_id"), dataString(fields, "model_display_source")); e != nil {
			return e
		}
		if uuid != "" {
			callFields := CloneRow(fields)
			if model := dataString(fields, "model"); model != "" {
				callFields["requested_model"] = model
			}
			fullPatch, contextPatch := claudeContextPatch(callFields)
			if _, e = tx.Exec(`UPDATE usage_records SET
				model=coalesce(nullif(json_extract(json_patch(data, `+claudeContextMerge+`), '$.model'),''),model),
				data=json_patch(data, `+claudeContextMerge+`)
				WHERE session_id=@session AND json_extract(data,'$.client_kind')='claude_code'
				AND json_extract(data,'$.node_id')=@node
				AND data<>json_patch(data, `+claudeContextMerge+`)`,
				fullPatch, contextPatch, sql.Named("session", session), sql.Named("node", ns+"uuid:"+uuid)); e != nil {
				return e
			}
		}
		fullPatch, contextPatch := claudeContextPatch(fields)
		if _, e = tx.Exec(`UPDATE claude_requests SET data=json_patch(data, `+claudeContextMerge+`)
			WHERE session_id=@session AND ((@uuid<>'' AND json_extract(data,'$.user_uuid')=@uuid) OR (@prompt<>'' AND id=@request))
			AND data<>json_patch(data, `+claudeContextMerge+`)`,
			fullPatch, contextPatch, sql.Named("session", session), sql.Named("uuid", uuid), sql.Named("prompt", prompt), sql.Named("request", ns+"request:"+prompt)); e != nil {
			return e
		}
		if dataString(fields, "model_display_name") != "" {
			requests, err := queryJSON(tx, "SELECT data FROM claude_requests WHERE session_id=? AND ((?<>'' AND json_extract(data,'$.user_uuid')=?) OR (?<>'' AND id=?))", session, uuid, uuid, prompt, ns+"request:"+prompt)
			if err != nil {
				return err
			}
			for _, request := range requests {
				calls, err := queryJSON(tx, "SELECT data FROM usage_records WHERE json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.claude_request_id')=? AND coalesce(json_extract(data,'$.model_display_name'),'')='' AND coalesce(json_extract(data,'$.is_subagent'),0)=0", request["id"])
				if err != nil {
					return err
				}
				for _, call := range calls {
					if err := s.saveClaudeRecord(tx, call); err != nil {
						return err
					}
				}
			}
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
