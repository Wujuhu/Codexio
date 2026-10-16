package backend

// Claude Code transcripts remain an independent read-only source. Reuse the
// existing collector's bounded lines, file identity, checkpoints and discovery;
// unlike Codex origin reconciliation, deletion never removes metered history.
import (
	"bufio"
	"context"
	"database/sql"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

func normalizedClient(v string) string {
	if v == "claude_code" {
		return v
	}
	return "codex"
}
func (s *Store) claudeSchema() error {
	_, e := s.db.Exec(`CREATE TABLE IF NOT EXISTS claude_revisions(kind TEXT PRIMARY KEY,revision INTEGER NOT NULL);
 INSERT OR IGNORE INTO claude_revisions VALUES('calls',0);
 CREATE TRIGGER IF NOT EXISTS claude_call_insert AFTER INSERT ON usage_records WHEN json_extract(NEW.data,'$.client_kind')='claude_code' BEGIN UPDATE claude_revisions SET revision=revision+1 WHERE kind='calls'; END;
 CREATE TRIGGER IF NOT EXISTS claude_call_update AFTER UPDATE ON usage_records WHEN json_extract(NEW.data,'$.client_kind')='claude_code' OR json_extract(OLD.data,'$.client_kind')='claude_code' BEGIN UPDATE claude_revisions SET revision=revision+1 WHERE kind='calls'; END;
 CREATE TRIGGER IF NOT EXISTS claude_call_delete AFTER DELETE ON usage_records WHEN json_extract(OLD.data,'$.client_kind')='claude_code' BEGIN UPDATE claude_revisions SET revision=revision+1 WHERE kind='calls'; END;
 CREATE TABLE IF NOT EXISTS claude_search(uuid TEXT NOT NULL,path TEXT NOT NULL,text TEXT NOT NULL,generation TEXT NOT NULL,PRIMARY KEY(uuid,path));
 CREATE VIRTUAL TABLE IF NOT EXISTS claude_body_search USING fts5(uuid UNINDEXED,path UNINDEXED,text,content='claude_search',content_rowid='rowid');
 CREATE TRIGGER IF NOT EXISTS claude_search_insert AFTER INSERT ON claude_search BEGIN INSERT INTO claude_body_search(rowid,uuid,path,text) VALUES(NEW.rowid,NEW.uuid,NEW.path,NEW.text); END;
 CREATE TRIGGER IF NOT EXISTS claude_search_delete AFTER DELETE ON claude_search BEGIN INSERT INTO claude_body_search(claude_body_search,rowid,uuid,path,text) VALUES('delete',OLD.rowid,OLD.uuid,OLD.path,OLD.text); END;
 CREATE TRIGGER IF NOT EXISTS claude_search_update AFTER UPDATE ON claude_search BEGIN INSERT INTO claude_body_search(claude_body_search,rowid,uuid,path,text) VALUES('delete',OLD.rowid,OLD.uuid,OLD.path,OLD.text); INSERT INTO claude_body_search(rowid,uuid,path,text) VALUES(NEW.rowid,NEW.uuid,NEW.path,NEW.text); END;
 CREATE TABLE IF NOT EXISTS claude_nodes(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,request_id TEXT NOT NULL,data TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS claude_nodes_request ON claude_nodes(request_id);
 CREATE TABLE IF NOT EXISTS claude_requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,data TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS claude_requests_session ON claude_requests(session_id);
 CREATE TABLE IF NOT EXISTS claude_locators(item_id TEXT NOT NULL,uuid TEXT NOT NULL,path TEXT NOT NULL,offset INTEGER NOT NULL,identity TEXT NOT NULL,line_hash TEXT NOT NULL,available INTEGER NOT NULL,generation TEXT NOT NULL,PRIMARY KEY(item_id,uuid,path));
 CREATE INDEX IF NOT EXISTS claude_locators_path ON claude_locators(path);
 CREATE INDEX IF NOT EXISTS claude_locators_path_available ON claude_locators(path,available);
 CREATE INDEX IF NOT EXISTS claude_timing_pending ON usage_records(id) WHERE coalesce(json_extract(data,'$.call_timing_checked'),0)=0 AND coalesce(json_extract(data,'$.is_subagent'),0)=0 AND json_extract(data,'$.client_kind')='claude_code';
 CREATE INDEX IF NOT EXISTS claude_response_scope ON usage_records(json_extract(data,'$.client_kind'),json_extract(data,'$.response_id'),json_extract(data,'$.claude_session_scope'));
 CREATE INDEX IF NOT EXISTS claude_records_request ON usage_records(json_extract(data,'$.client_kind'),json_extract(data,'$.claude_request_id'));
 CREATE TRIGGER IF NOT EXISTS claude_request_insert AFTER INSERT ON claude_requests BEGIN UPDATE usage_revisions SET revision=revision+1 WHERE kind='ledger'; INSERT INTO usage_query_changes(kind,item_id) VALUES('claude_request',NEW.id); END;
 CREATE TRIGGER IF NOT EXISTS claude_request_update AFTER UPDATE ON claude_requests BEGIN UPDATE usage_revisions SET revision=revision+1 WHERE kind='ledger'; INSERT INTO usage_query_changes(kind,item_id) VALUES('claude_request',NEW.id); END;`)
	return e
}
func claudeRoot() string {
	if v := os.Getenv("CLAUDE_CONFIG_DIR"); strings.TrimSpace(v) != "" {
		return systemExpandPath(v)
	}
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".claude")
}
func claudeNamespace(root string) string {
	return "claude:" + HashString(filepath.Clean(root))[:16] + ":"
}
func (s *Store) ClientStatus(client string) Row {
	if normalizedClient(client) == "codex" {
		r := s.Status()
		r["client_kind"] = "codex"
		return r
	}
	s.mu.RLock()
	r := CloneRow(s.claudeStatus)
	r["updated_at"] = s.claudeLastScan
	s.mu.RUnlock()
	r["client_kind"] = "claude_code"
	r["generation"] = s.Generation()
	return r
}
func (s *Store) collectClaude(ctx context.Context, force bool) error {
	root := claudeRoot()
	projects := filepath.Join(root, "projects")
	status := Row{"status": "ready", "source_status": "ready", "client_kind": "claude_code"}
	defer func() {
		s.mu.Lock()
		prior := dataJSON(s.claudeStatus)
		s.claudeStatus = status
		s.mu.Unlock()
		if prior != dataJSON(status) {
			s.generation.Add(1)
		}
	}()
	if err := s.observeClaudePriceLabels(root); err != nil {
		return err
	}
	info, e := os.Stat(projects)
	if e != nil || !info.IsDir() {
		status["source_status"] = "unavailable"
		status["status"] = "unavailable"
		if e != nil && !os.IsNotExist(e) {
			status["status"] = "error"
			status["error"] = "Claude Code 数据目录不可读"
		}
		// Unreadability is not deletion. Both suppress live-body availability;
		// only confirmed absence clears source-aware searchable body text.
		return s.invalidateClaudePath(projects+string(os.PathSeparator), os.IsNotExist(e), true)
	}
	rediscover := force || time.Since(s.claudeDiscovery) > 5*time.Minute
	if rediscover {
		s.claudeDiscovery = time.Now()
	}
	paths, e := s.discover(projects, rediscover)
	if e != nil {
		status["status"] = "error"
		status["source_status"] = "error"
		status["error"] = "Claude Code 数据目录不可读"
		return s.invalidateClaudePath(projects+string(os.PathSeparator), false, true)
	}
	sort.Strings(paths)
	if len(paths) == 0 {
		status["source_status"] = "empty"
	}
	seen := map[string]bool{}
	failed := 0
	partial := false
	for _, path := range paths {
		if e = ctx.Err(); e != nil {
			return e
		}
		seen[path] = true
		malformed, err := s.scanClaudeFile(ctx, root, path, force)
		partial = partial || malformed
		if err != nil {
			if invalidErr := s.invalidateClaudePath(path, os.IsNotExist(err), false); invalidErr != nil {
				return invalidErr
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			failed++
		}
	}
	ns := claudeNamespace(root)
	var integrityVersion string
	_ = s.db.QueryRow("SELECT data FROM usage_meta WHERE key=?", ns+"integrity_version").Scan(&integrityVersion)
	if integrityVersion != "1" {
		entries, err := s.db.Query("SELECT DISTINCT path FROM claude_locators WHERE instr(path,?)=1", projects+string(os.PathSeparator))
		if err != nil {
			return err
		}
		legacyPaths := []string{}
		for entries.Next() {
			var path string
			if err = entries.Scan(&path); err != nil {
				entries.Close()
				return err
			}
			legacyPaths = append(legacyPaths, path)
		}
		entries.Close()
		for _, path := range legacyPaths {
			if _, err = s.db.Exec("UPDATE usage_cursors SET data=json_set(data,'$.path',?) WHERE key=?", path, ns+"file:"+HashString(path)); err != nil {
				return err
			}
		}
	}
	rows, e := s.db.Query("SELECT json_extract(data,'$.path') FROM usage_cursors WHERE instr(key,?)=1 AND json_extract(data,'$.path') IS NOT NULL", ns+"file:")
	if e != nil {
		return e
	}
	missing := []string{}
	unreadable := []string{}
	for rows.Next() {
		var path string
		if e = rows.Scan(&path); e != nil {
			rows.Close()
			return e
		}
		if !seen[path] {
			if _, err := os.Stat(path); os.IsNotExist(err) {
				missing = append(missing, path)
			} else if err != nil {
				unreadable = append(unreadable, path)
			}
		}
	}
	rows.Close()
	for _, path := range unreadable {
		if e = s.invalidateClaudePath(path, false, false); e != nil {
			return e
		}
	}
	for _, path := range missing {
		if e = s.invalidateClaudePath(path, true, false); e != nil {
			return e
		}
	}
	var repairPending string
	_ = s.db.QueryRow("SELECT data FROM usage_meta WHERE key=?", ns+"repair_pending").Scan(&repairPending)
	if repairPending == "true" || integrityVersion != "1" {
		tx, err := s.db.Begin()
		if err != nil {
			return err
		}
		err = s.resolveClaudeOwnership(tx, ns)
		if err == nil {
			_, err = tx.Exec("INSERT INTO usage_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", ns+"integrity_version", "1")
		}
		if err == nil {
			err = tx.Commit()
		} else {
			tx.Rollback()
		}
		if err != nil {
			return err
		}
	}
	if e = s.recoverClaudeCallTiming(ctx, ns); e != nil {
		return e
	}
	if failed > 0 || partial {
		status["status"] = "error"
		status["source_status"] = "partial"
		status["error"] = "部分 Claude Code 记录不可读或格式不完整"
	}
	now := time.Now()
	stamp := ledgerStamp(now)
	display := now.Format("2006-01-02 15:04 -0700")
	s.mu.Lock()
	s.claudeLastScan = stamp
	clockChanged := display != s.claudeLastDisplay
	s.claudeLastDisplay = display
	s.mu.Unlock()
	if clockChanged && s.options.ClientScanUpdated != nil {
		s.options.ClientScanUpdated("claude_code", stamp)
	}
	status["files"] = len(paths)
	status["current_sessions"] = claudeCurrentSessions(root)
	incomplete, err := s.claudeIncompleteCount()
	if err != nil {
		return err
	}
	status["incomplete_records"] = incomplete
	if incomplete > 0 && status["source_status"] == "ready" {
		status["source_status"] = "partial"
	}
	return nil
}
func (s *Store) scanClaudeFile(ctx context.Context, root, path string, force bool) (bool, error) {
	stat, e := os.Stat(path)
	if e != nil {
		return false, e
	}
	key := claudeNamespace(root) + "file:" + HashString(path)
	var raw string
	_ = s.db.QueryRow("SELECT data FROM usage_cursors WHERE key=?", key).Scan(&raw)
	cursor := dataRow(raw)
	f, e := os.Open(path)
	if e != nil {
		return false, e
	}
	defer f.Close()
	identity := ledgerFileIdentity(f)
	offset := ValueInt(cursor["offset"])
	if !force && dataString(cursor, "identity") == identity && ValueInt(cursor["size"]) == stat.Size() && ValueInt(cursor["mtime"]) == stat.ModTime().UnixNano() {
		if ValueBool(cursor["source_unavailable"]) || ValueInt(cursor["search_version"]) != 1 {
			complete, err := s.restoreClaudeLocators(ctx, path, identity, dataString(cursor, "generation"), ValueInt(cursor["search_version"]) != 1)
			if err != nil {
				return false, err
			}
			cursor["source_unavailable"] = false
			if complete {
				cursor["search_version"] = 1
			}
			if _, err = s.db.Exec("UPDATE usage_cursors SET data=? WHERE key=?", dataJSON(cursor), key); err != nil {
				return false, err
			}
		}
		return ValueBool(cursor["partial"]), nil
	}
	valid := !force && dataString(cursor, "identity") == identity && offset >= 0 && offset <= stat.Size() && dataString(cursor, "checkpoint") == checkpointHash(f, offset) && dataString(cursor, "prefix") == prefixHash(f, offset) && !(offset == stat.Size() && ValueInt(cursor["mtime"]) != stat.ModTime().UnixNano())
	state := ValueRow(cursor["state"])
	generation := dataString(cursor, "generation")
	if !valid {
		offset = 0
		state = Row{}
		generation = HashString(fmt.Sprint(identity, stat.ModTime().UnixNano()))[:20]
	}
	_, e = f.Seek(offset, io.SeekStart)
	if e != nil {
		return false, e
	}
	tx, e := s.db.Begin()
	if e != nil {
		return false, e
	}
	defer tx.Rollback()
	if !valid {
		if _, e = tx.Exec("UPDATE claude_locators SET available=0 WHERE path=?", path); e != nil {
			return false, e
		}
		if _, e = tx.Exec("DELETE FROM claude_search WHERE path=?", path); e != nil {
			return false, e
		}
	}
	reader := bufio.NewReaderSize(f, 65536)
	malformed := valid && ValueBool(cursor["partial"])
	for {
		if e = ctx.Err(); e != nil {
			return false, e
		}
		start := offset
		line, err := readBoundedLine(reader, rolloutLineLimit)
		if err == io.EOF {
			break
		}
		if err != nil {
			return false, err
		}
		offset += int64(len(line))
		entry, err := DecodeRow(line)
		if err != nil {
			malformed = true
			continue
		}
		if e = s.claudeEntry(tx, entry, state, root, path, identity, generation, start, HashString(string(line))); e != nil {
			return false, e
		}
	}
	restoreAfterAppend := valid && (ValueBool(cursor["source_unavailable"]) || ValueInt(cursor["search_version"]) != 1)
	searchPending := valid && ValueInt(cursor["search_version"]) != 1
	searchVersion := 1
	if searchPending {
		searchVersion = 0
	}
	cursor = Row{"path": path, "search_version": searchVersion, "source_unavailable": restoreAfterAppend, "identity": identity, "offset": offset, "size": stat.Size(), "mtime": stat.ModTime().UnixNano(), "checkpoint": checkpointHash(f, offset), "prefix": prefixHash(f, offset), "generation": generation, "state": state, "partial": malformed}
	_, e = tx.Exec("INSERT INTO usage_cursors VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", key, dataJSON(cursor))
	if e != nil {
		return false, e
	}
	// Late parents or copied files may make an earlier unresolved chain recoverable.
	if e = s.resolveClaudeOwnership(tx, claudeNamespace(root)); e != nil {
		return false, e
	}
	if e = tx.Commit(); e != nil {
		return false, e
	}
	if restoreAfterAppend {
		complete, err := s.restoreClaudeLocators(ctx, path, identity, generation, searchPending)
		if err != nil {
			return false, err
		}
		cursor["source_unavailable"] = false
		if complete {
			cursor["search_version"] = 1
		}
		if _, err = s.db.Exec("UPDATE usage_cursors SET data=? WHERE key=?", dataJSON(cursor), key); err != nil {
			return false, err
		}
	}
	if !valid && raw != "" {
		s.generation.Add(1)
	}
	return malformed, nil
}
func claudeText(content any) (string, bool) {
	if v, ok := content.(string); ok {
		return v, true
	}
	text := []string{}
	complete := true
	for _, part := range ValueRows(content) {
		switch dataString(part, "type") {
		case "text":
			text = append(text, dataString(part, "text"))
		case "image", "document":
		case "tool_result", "tool_use", "thinking", "redacted_thinking":
		default:
			complete = false
		}
	}
	return strings.Join(text, "\n"), complete
}
func claudeHuman(e Row) bool {
	if dataString(e, "type") != "user" || ValueBool(e["isSidechain"]) || ValueBool(e["isMeta"]) || ValueBool(e["turnCompanion"]) {
		return false
	}
	for _, part := range ValueRows(ValueRow(e["message"])["content"]) {
		if dataString(part, "type") == "tool_result" {
			return false
		}
	}
	return dataString(ValueRow(e["origin"]), "kind") == "human"
}
func claudeLookupNode(tx *sql.Tx, id string) Row {
	var raw string
	_ = tx.QueryRow("SELECT data FROM claude_nodes WHERE id=?", id).Scan(&raw)
	return dataRow(raw)
}
func claudeGetRequest(tx *sql.Tx, id string) Row {
	var raw string
	_ = tx.QueryRow("SELECT data FROM claude_requests WHERE id=?", id).Scan(&raw)
	return dataRow(raw)
}
func claudeSaveRequest(tx *sql.Tx, r Row) error {
	_, e := tx.Exec("INSERT INTO claude_requests VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE data<>excluded.data", r["id"], r["session_id"], dataJSON(r))
	return e
}
func claudeLocate(tx *sql.Tx, item, uuid, path, identity, generation string, offset int64, hash string) error {
	_, e := tx.Exec("INSERT INTO claude_locators VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(item_id,uuid,path) DO UPDATE SET offset=excluded.offset,identity=excluded.identity,line_hash=excluded.line_hash,available=1,generation=excluded.generation", item, uuid, path, offset, identity, hash, generation)
	return e
}
func (s *Store) claudeEntry(tx *sql.Tx, e, state Row, root, path, identity, generation string, offset int64, hash string) error {
	ns := claudeNamespace(root)
	originalSession := firstString(e["sessionId"], state["original_session"], strings.TrimSuffix(filepath.Base(path), ".jsonl"))
	session := ns + "session:" + originalSession
	// An explicit subagent identity gets its own namespace even when transcript sessionId names the host.
	agent := dataString(e, "agentId")
	if agent == "" && strings.Contains(filepath.ToSlash(path), "/subagents/") {
		agent = strings.TrimPrefix(strings.TrimSuffix(filepath.Base(path), ".jsonl"), "agent-")
	}
	if agent == "" && ValueBool(e["isSidechain"]) {
		agent = "sidechain:" + HashString(path)[:16]
	}
	if agent != "" {
		session += ":agent:" + agent
	}
	state["original_session"] = originalSession
	state["session_id"] = session
	if origin := dataString(e, "entrypoint"); origin != "" {
		state["client_origin"] = origin
	}
	timestamp := stamp(e["timestamp"])
	uuid := dataString(e, "uuid")
	parent := dataString(e, "parentUuid")
	nodeID := ns + "uuid:" + uuid
	typ := dataString(e, "type")
	message := ValueRow(e["message"])
	if typ == "custom-title" {
		title := clip(dataString(e, "customTitle"), 1024)
		if title != "" {
			_, err := tx.Exec("INSERT INTO usage_session_titles VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title WHERE title<>excluded.title", session, title)
			return err
		}
		return nil
	}
	if uuid == "" {
		return nil
	}
	oldNode := claudeLookupNode(tx, nodeID)
	parentNode := claudeLookupNode(tx, ns+"uuid:"+parent)
	if canonical := dataString(oldNode, "session_id"); canonical != "" {
		session = canonical
	} else if !claudeHuman(e) && agent == "" && dataString(parentNode, "session_id") != "" && !ValueBool(parentNode["is_subagent"]) {
		session = dataString(parentNode, "session_id")
	}
	if canonicalAgent := dataString(oldNode, "claude_agent_id"); canonicalAgent != "" {
		agent = canonicalAgent
	} else if at := strings.Index(session, ":agent:"); at >= 0 {
		agent = session[at+7:]
	}
	request := firstString(oldNode["request_id"], parentNode["request_id"])

	human := claudeHuman(e)
	if human {
		prompt := firstString(e["promptId"], uuid)
		candidate := ns + "request:" + prompt
		request = candidate
		if ValueBool(oldNode["human"]) {
			request = firstString(oldNode["request_id"], candidate)
		}
		r := claudeGetRequest(tx, request)
		if len(r) == 0 {
			text, _ := claudeText(message["content"])
			r = Row{"id": request, "client_kind": "claude_code", "session_id": session, "turn_id": prompt, "timestamp": timestamp, "started_at": timestamp, "prompt_preview": clip(text, 320), "request_status": "unknown", "status": "unknown", "record_kind": "user_request", "has_user_message": true, "source_id": "local:claude:" + HashString(root)[:16], "client_origin": state["client_origin"], "user_uuid": uuid, "model": e["requestedModel"], "reasoning_effort": e["effort"]}
		}
		if err := claudeSaveRequest(tx, r); err != nil {
			return err
		}
		state["request_id"] = request
		if err := claudeLocate(tx, request, uuid, path, identity, generation, offset, hash); err != nil {
			return err
		}
	}
	if request == "" && typ == "user" && !human {
		if prompt := dataString(e, "promptId"); prompt != "" {
			candidate := ns + "request:" + prompt
			owner := claudeGetRequest(tx, candidate)
			if dataString(owner, "session_id") == ns+"session:"+originalSession {
				request = candidate
			}
		}
	}
	requestedModel := dataString(e, "requestedModel")
	if !human && request != "" && request == dataString(parentNode, "request_id") {
		requestedModel = firstString(e["requestedModel"], parentNode["requested_model"])
	}
	attachment := ValueRow(e["attachment"])
	if typ == "attachment" && dataString(attachment, "type") == "model" {
		requestedModel = dataString(ValueRow(attachment["identity"]), "modelId")
		if request != "" && agent == "" && !ValueBool(e["isSidechain"]) {
			owner := claudeGetRequest(tx, request)
			if len(owner) > 0 && dataString(owner, "model") == "" && requestedModel != "" {
				owner["model"] = requestedModel
				if err := claudeSaveRequest(tx, owner); err != nil {
					return err
				}
			}
		}
	}
	node := Row{"id": nodeID, "uuid": uuid, "parent_id": ns + "uuid:" + parent, "request_id": request, "session_id": firstString(oldNode["session_id"], session), "record_id": oldNode["record_id"], "owner_session_verified": false, "claude_agent_id": agent, "event_applied_owner": oldNode["event_applied_owner"], "synthetic_error": typ == "assistant" && (ValueBool(e["isApiErrorMessage"]) || dataString(message, "model") == "<synthetic>"), "requested_model": requestedModel, "type": typ, "human": human, "subtype": e["subtype"], "duration_ms": e["durationMs"], "prompt_id": e["promptId"], "original_session": ns + "session:" + originalSession, "timestamp": timestamp, "is_subagent": agent != "" || ValueBool(e["isSidechain"]) || ValueBool(oldNode["is_subagent"])}
	if _, err := tx.Exec("INSERT INTO claude_nodes VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET request_id=excluded.request_id,data=excluded.data WHERE data<>excluded.data", nodeID, node["session_id"], request, dataJSON(node)); err != nil {
		return err
	}
	if human {
		if err := claudeSaveSearch(tx, e, path, generation); err != nil {
			return err
		}
	}
	if typ != "assistant" {
		return nil
	}
	if err := claudeSaveSearch(tx, e, path, generation); err != nil {
		return err
	}
	synthetic := ValueBool(e["isApiErrorMessage"]) || dataString(message, "model") == "<synthetic>"
	if request != "" {
		r := claudeGetRequest(tx, request)
		if len(r) > 0 && agent == "" && !ValueBool(e["isSidechain"]) {
			if dataString(r, "model") == "" && dataString(e, "requestedModel") != "" {
				r["model"] = e["requestedModel"]
			}
			if dataString(r, "reasoning_effort") == "" && dataString(e, "effort") != "" {
				r["reasoning_effort"] = requestEffortName(e["effort"])
			}
			if err := claudeSaveRequest(tx, r); err != nil {
				return err
			}
		}
	}
	if synthetic {
		return claudeLocate(tx, nodeID, uuid, path, identity, generation, offset, hash)
	}
	response := dataString(message, "id")
	usage := ValueRow(message["usage"])
	if response == "" {
		return nil
	}
	// Persist UUID ancestry before reply identity; the repair transaction rekeys
	// early unassigned IDs into the same explicit-owner canonical identity.
	sessionScope := session
	id := claudeReplyID(ns, request, sessionScope, agent, response)
	if previous := dataString(oldNode, "record_id"); previous != "" {
		id = previous
	}
	node["record_id"] = id
	if _, err := tx.Exec("UPDATE claude_nodes SET data=? WHERE id=?", dataJSON(node), nodeID); err != nil {
		return err
	}
	r := Row{"id": id, "response_id": response, "client_kind": "claude_code", "client_origin": state["client_origin"], "session_id": firstString(oldNode["session_id"], session), "claude_request_id": request, "claude_session_scope": sessionScope, "claude_agent_id": agent, "turn_id": "", "timestamp": timestamp, "requested_model": requestedModel, "returned_model": message["model"], "upstream_model": message["model"], "upstream_mismatched": false, "model": requestedModel, "reasoning_effort": requestEffortName(e["effort"]), "service_tier": nil, "cost_usd": nil, "pricing_status": "unpriced", "quality": "response", "source_id": "local:claude:" + HashString(root)[:16], "source_path": path, "is_subagent": agent != "" || ValueBool(e["isSidechain"]), "parent_uuid": parent, "node_id": nodeID, "stop_reason": message["stop_reason"], "duration_ms": nil, "tps": nil, "ttft": nil, "model_context_window": nil}
	in, ki := knownCount(usage["input_tokens"])
	read, kr := knownCount(usage["cache_read_input_tokens"])
	write, kw := knownCount(usage["cache_creation_input_tokens"])
	out, ko := knownCount(usage["output_tokens"])
	creation := ValueRow(usage["cache_creation"])
	r["cache_write_5m_tokens"] = creation["ephemeral_5m_input_tokens"]
	r["cache_write_1h_tokens"] = creation["ephemeral_1h_input_tokens"]
	speed := strings.ToLower(firstString(usage["speed"], e["speed"]))
	if speed == "fast" {
		r["service_tier"] = "priority"
	} else if speed == "standard" {
		r["service_tier"] = "default"
	} else if fast, ok := e["fastMode"].(bool); ok {
		if fast {
			r["service_tier"] = "priority"
		} else {
			r["service_tier"] = "default"
		}
	} else if fast, ok := e["fast_mode"].(bool); ok {
		if fast {
			r["service_tier"] = "priority"
		} else {
			r["service_tier"] = "default"
		}
	}
	for _, key := range []string{"auth_mode", "billing_source"} {
		if e[key] != nil {
			r[key] = e[key]
		}
	}
	r["raw_input_tokens"] = usage["input_tokens"]
	r["cached_input_tokens"] = usage["cache_read_input_tokens"]
	r["cache_write_input_tokens"] = usage["cache_creation_input_tokens"]
	r["output_tokens"] = usage["output_tokens"]
	r["reasoning_output_tokens"] = ValueRow(usage["output_tokens_details"])["thinking_tokens"]
	if ki && kr && kw {
		r["input_tokens"] = in + read + write
		if ko {
			r["total_tokens"] = in + read + write + out
		}
	}
	r["input_complete"] = ki && kr && kw
	r["usage_complete"] = ki && kr && kw && ko
	r["call_timing"] = claudeTimingObservation(e)
	r["call_timing_checked"] = true
	r["usage_terminal"] = dataString(message, "stop_reason") != ""
	r["final_missing_fields"] = []string{}
	if ValueBool(r["usage_terminal"]) {
		missing := []string{}
		for _, part := range []struct {
			field string
			known bool
		}{{"raw_input_tokens", ki}, {"cached_input_tokens", kr}, {"cache_write_input_tokens", kw}, {"output_tokens", ko}} {
			if !part.known {
				missing = append(missing, part.field)
			}
		}
		r["final_missing_fields"] = missing
	}
	terminalFacts := Row{}
	if ValueBool(r["usage_terminal"]) {
		for _, field := range append(append([]string{}, tokenFields...), "raw_input_tokens") {
			if _, known := knownCount(r[field]); known {
				terminalFacts[field] = r[field]
			}
		}
	}
	r["terminal_usage_values"] = terminalFacts
	r["request_status"] = "unknown"
	if ValueBool(r["usage_terminal"]) {
		r["request_status"] = "completed"
	}
	r["usage_zero"] = ki && kr && kw && ko && in+read+write+out == 0
	if request != "" {
		owner := claudeGetRequest(tx, request)
		r["prompt_preview"] = owner["prompt_preview"]
	}
	text, _ := claudeText(message["content"])
	r["output_preview"] = clip(text, 320)
	if err := s.saveClaudeRecord(tx, r); err != nil {
		return err
	}
	if err := claudeLocate(tx, id, uuid, path, identity, generation, offset, hash); err != nil {
		return err
	}
	if request != "" && agent == "" && !ValueBool(e["isSidechain"]) {
		return claudeLocate(tx, request, uuid, path, identity, generation, offset, hash)
	}
	return nil
}
func (s *Store) saveClaudeRecord(tx *sql.Tx, r Row) error {
	var raw string
	_ = tx.QueryRow("SELECT data FROM usage_records WHERE id=?", r["id"]).Scan(&raw)
	r = mergeClaudeUsage(dataRow(raw), r)
	payload := dataJSON(r)
	if payload != raw {
		if _, e := tx.Exec("INSERT INTO usage_records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,model=excluded.model,session_id=excluded.session_id,data=excluded.data", r["id"], dataString(r, "timestamp"), firstString(r["model"], "unknown"), dataString(r, "session_id"), payload); e != nil {
			return e
		}
	}
	_, e := tx.Exec("INSERT OR IGNORE INTO usage_record_sources VALUES(?,?)", r["id"], r["source_id"])
	return e
}
func (s *Store) resolveClaudeOwnership(tx *sql.Tx, ns string) error {
	if _, e := tx.Exec(`UPDATE claude_nodes AS n SET request_id=(SELECT q.id FROM claude_requests q WHERE q.id=?||'request:'||json_extract(n.data,'$.prompt_id') AND q.session_id=json_extract(n.data,'$.original_session')),data=json_set(data,'$.request_id',(SELECT q.id FROM claude_requests q WHERE q.id=?||'request:'||json_extract(n.data,'$.prompt_id') AND q.session_id=json_extract(n.data,'$.original_session'))) WHERE request_id='' AND json_extract(data,'$.type')='user' AND EXISTS(SELECT 1 FROM claude_requests q WHERE q.id=?||'request:'||json_extract(n.data,'$.prompt_id') AND q.session_id=json_extract(n.data,'$.original_session'))`, ns, ns, ns); e != nil {
		return e
	}
	// A bounded number of SQL passes closes late explicit parent chains without
	// loading all transcripts or guessing from timestamps.
	for i := 0; i < 64; i++ {
		res, e := tx.Exec(`UPDATE claude_nodes AS n SET request_id=(SELECT p.request_id FROM claude_nodes p WHERE p.id=json_extract(n.data,'$.parent_id')),data=json_set(data,'$.request_id',(SELECT p.request_id FROM claude_nodes p WHERE p.id=json_extract(n.data,'$.parent_id'))) WHERE request_id='' AND instr(id,?)=1 AND EXISTS(SELECT 1 FROM claude_nodes p WHERE p.id=json_extract(n.data,'$.parent_id') AND p.request_id<>'')`, ns)
		if e != nil {
			return e
		}
		n, _ := res.RowsAffected()
		if n == 0 {
			break
		}
	}
	if e := s.verifyClaudeOwnerSessions(tx, ns); e != nil {
		return e
	}
	_, e := tx.Exec(`UPDATE usage_records AS r SET session_id=(SELECT n.session_id FROM claude_nodes n WHERE n.id=json_extract(r.data,'$.node_id')),data=json_set(data,'$.claude_request_id',(SELECT n.request_id FROM claude_nodes n WHERE n.id=json_extract(r.data,'$.node_id')),'$.session_id',(SELECT n.session_id FROM claude_nodes n WHERE n.id=json_extract(r.data,'$.node_id')),'$.claude_session_scope',(SELECT n.session_id FROM claude_nodes n WHERE n.id=json_extract(r.data,'$.node_id')),'$.claude_agent_id',(SELECT json_extract(n.data,'$.claude_agent_id') FROM claude_nodes n WHERE n.id=json_extract(r.data,'$.node_id'))) WHERE json_extract(data,'$.client_kind')='claude_code' AND EXISTS(SELECT 1 FROM claude_nodes n WHERE n.id=json_extract(r.data,'$.node_id') AND n.request_id<>'' AND (coalesce(json_extract(r.data,'$.claude_request_id'),'')<>n.request_id OR r.session_id<>n.session_id))`)
	if e != nil {
		return e
	}
	var pending int
	if e = tx.QueryRow(`SELECT EXISTS(SELECT 1 FROM claude_nodes n JOIN claude_nodes p ON p.id=json_extract(n.data,'$.parent_id') WHERE instr(n.id,?)=1 AND n.request_id='' AND p.request_id<>'')`, ns).Scan(&pending); e != nil {
		return e
	}
	if _, e = tx.Exec("INSERT INTO usage_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", ns+"repair_pending", dataJSON(pending > 0)); e != nil {
		return e
	}
	if e = s.reconcileClaudeReplies(tx, ns); e != nil {
		return e
	}
	if e = s.applyClaudeEvents(tx, ns); e != nil {
		return e
	}
	_, e = tx.Exec(`INSERT OR IGNORE INTO claude_locators SELECT json_extract(r.data,'$.claude_request_id'),l.uuid,l.path,l.offset,l.identity,l.line_hash,l.available,l.generation FROM usage_records r JOIN claude_locators l ON l.item_id=r.id WHERE json_extract(r.data,'$.client_kind')='claude_code' AND coalesce(json_extract(r.data,'$.claude_request_id'),'')<>'' AND coalesce(json_extract(r.data,'$.is_subagent'),0)=0 AND r.id IN(SELECT item_id FROM usage_query_changes WHERE kind='record')`)
	return e
}

func claudeCurrentSessions(root string) []Row {
	entries, e := os.ReadDir(filepath.Join(root, "sessions"))
	if e != nil {
		return []Row{}
	}
	result := []Row{}
	for index, entry := range entries {
		if index >= 256 {
			break
		}
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		value, e := systemReadJSON(filepath.Join(root, "sessions", entry.Name()), 65536)
		if e != nil || !claudeVerifiedProcess(value) {
			continue
		}
		session := dataString(value, "sessionId")
		if session == "" {
			continue
		}
		// Metadata proves current session state, not which historical request owns
		// it. Keep it separate; idle never turns old unknown requests into completed.
		result = append(result, Row{"session_id": claudeNamespace(root) + "session:" + session, "status": value["status"], "status_updated_at": value["statusUpdatedAt"], "client_origin": value["entrypoint"], "process_verified": true})
	}
	return result
}
