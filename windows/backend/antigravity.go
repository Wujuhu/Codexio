package backend

import (
	"bufio"
	"context"
	"database/sql"
	"errors"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

const antigravityClient = "antigravity"
const antigravityParserVersion = 1

var antigravityUserRequest = regexp.MustCompile(`(?is)<USER_REQUEST>(.*?)</USER_REQUEST>`)
var antigravitySettingsChange = regexp.MustCompile(`(?is)<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>`)

func antigravityUserText(value string) string {
	if match := antigravityUserRequest.FindStringSubmatch(value); len(match) > 1 {
		return strings.TrimSpace(match[1])
	}
	return strings.TrimSpace(antigravitySettingsChange.ReplaceAllString(value, ""))
}

type antigravityScan struct {
	files              []string
	stamp              int64
	discovered         time.Time
	status             Row
	updated            string
	activityReconciled bool
}

func antigravityRoot() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".gemini", "antigravity", "brain")
}

func (s *Store) antigravitySchema() error {
	_, err := s.db.Exec(`CREATE TABLE IF NOT EXISTS antigravity_events(
		id TEXT PRIMARY KEY,session_id TEXT NOT NULL,timestamp TEXT NOT NULL,data TEXT NOT NULL);
		CREATE INDEX IF NOT EXISTS antigravity_events_session ON antigravity_events(session_id,timestamp);
		CREATE TABLE IF NOT EXISTS antigravity_requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,data TEXT NOT NULL);
		CREATE INDEX IF NOT EXISTS antigravity_requests_session ON antigravity_requests(session_id);
		CREATE INDEX IF NOT EXISTS antigravity_requests_start ON antigravity_requests(session_id,json_extract(data,'$.timestamp'));
		CREATE INDEX IF NOT EXISTS antigravity_requests_running ON antigravity_requests(json_extract(data,'$.running_observed_at'))
		WHERE json_extract(data,'$.status')='running';
		CREATE INDEX IF NOT EXISTS go_priced_antigravity_owner ON usage_priced_calls(json_extract(data,'$.antigravity_request_id'),timestamp,id)
		WHERE json_extract(data,'$.client_kind')='antigravity';`)
	return err
}

func (s *Store) collectAntigravity(ctx context.Context, force bool) error {
	root := antigravityRoot()
	status := Row{"status": "ready", "source_status": "ready", "client_kind": antigravityClient}
	defer func() {
		s.mu.Lock()
		changed := dataJSON(s.antigravity.status) != dataJSON(status)
		s.antigravity.status = status
		s.mu.Unlock()
		if changed {
			s.generation.Add(1)
		}
	}()
	info, err := os.Stat(root)
	if err != nil {
		status["source_status"] = "unavailable"
		if !errors.Is(err, os.ErrNotExist) {
			status["status"], status["error"] = "error", "Antigravity App 记录不可读"
		}
		return nil
	}
	if !info.IsDir() {
		status["source_status"] = "unavailable"
		return nil
	}
	if force || s.antigravity.stamp != info.ModTime().UnixNano() || time.Since(s.antigravity.discovered) >= 5*time.Minute {
		entries, err := os.ReadDir(root)
		if err != nil {
			return err
		}
		files := []string{}
		for _, entry := range entries {
			if entry.IsDir() && rolloutUUID.FindString(entry.Name()) == entry.Name() {
				for _, name := range []string{"transcript.jsonl", "transcript_full.jsonl"} {
					files = append(files, filepath.Join(root, entry.Name(), ".system_generated", "logs", name))
				}
			}
		}
		sort.Strings(files)
		s.antigravity.files, s.antigravity.stamp, s.antigravity.discovered = files, info.ModTime().UnixNano(), time.Now()
	}
	var problems []error
	files := 0
	for _, path := range s.antigravity.files {
		if err := ctx.Err(); err != nil {
			return err
		}
		if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
			continue
		}
		files++
		if err := s.scanAntigravityFile(ctx, path, force); err != nil {
			if len(problems) < 4 {
				problems = append(problems, errors.New("Antigravity App 记录读取失败"))
			}
		}
	}
	status["files"] = files
	if len(problems) == 0 {
		s.mu.Lock()
		s.antigravity.updated = ledgerStamp(time.Now())
		s.mu.Unlock()
	}
	if files == 0 {
		status["source_status"] = "empty"
	}
	if len(problems) > 0 {
		status["status"], status["source_status"], status["error"] = "error", "partial", "部分 Antigravity App 记录不可读"
	}
	return errors.Join(problems...)
}

func (s *Store) scanAntigravityFile(ctx context.Context, path string, force bool) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	session := "antigravity:app:" + filepath.Base(filepath.Dir(filepath.Dir(filepath.Dir(path))))
	key := "antigravity:app:file:" + HashString(path)
	var raw string
	_ = s.db.QueryRow("SELECT data FROM usage_cursors WHERE key=?", key).Scan(&raw)
	cursor := dataRow(raw)
	identity := ledgerFileIdentity(f)
	offset := ValueInt(cursor["offset"])
	if !force && ValueInt(cursor["parser_version"]) == antigravityParserVersion && dataString(cursor, "identity") == identity && ValueInt(cursor["size"]) == info.Size() && ValueInt(cursor["mtime"]) == info.ModTime().UnixNano() && (offset == info.Size() || ValueBool(cursor["partial_tail"])) {
		return nil
	}
	valid := !force && ValueInt(cursor["parser_version"]) == antigravityParserVersion && dataString(cursor, "identity") == identity && offset >= 0 && offset <= info.Size() && dataString(cursor, "checkpoint") == checkpointHash(f, offset) && dataString(cursor, "prefix") == prefixHash(f, offset) && !(offset == info.Size() && ValueInt(cursor["mtime"]) != info.ModTime().UnixNano())
	if valid && offset == info.Size() && ValueInt(cursor["mtime"]) == info.ModTime().UnixNano() {
		return nil
	}
	current := dataString(cursor, "request")
	if !valid {
		offset, current = 0, ""
	}
	if _, err = f.Seek(offset, io.SeekStart); err != nil {
		return err
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	reader := bufio.NewReaderSize(f, 65536)
	partial := false
	for {
		if err = ctx.Err(); err != nil {
			return err
		}
		line, readErr := readBoundedLine(reader, rolloutLineLimit)
		if readErr == io.EOF && len(line) == 0 {
			break
		}
		if readErr != nil && readErr != io.EOF {
			return readErr
		}
		row, parseErr := DecodeRow(line)
		if readErr == io.EOF && parseErr != nil {
			partial = true
			break
		}
		if parseErr != nil {
			return errors.New("Antigravity App 记录格式无效")
		}
		if err = s.antigravityEntry(tx, row, session, &current); err != nil {
			return err
		}
		offset += int64(len(line))
	}
	cursor = Row{"path": path, "identity": identity, "offset": offset, "size": info.Size(), "mtime": info.ModTime().UnixNano(), "prefix": prefixHash(f, offset), "checkpoint": checkpointHash(f, offset), "request": current, "parser_version": antigravityParserVersion, "partial_tail": partial}
	if _, err = tx.Exec("INSERT INTO usage_cursors VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", key, dataJSON(cursor)); err != nil {
		return err
	}
	return tx.Commit()
}

func antigravityEventID(session string, row Row) string {
	return session + ":event:" + pythonHash([]any{row["created_at"], row["step_index"]})
}

func antigravityComplete(row Row) bool {
	for _, field := range ValueStrings(row["truncated_fields"]) {
		if field == "content" {
			return false
		}
	}
	return true
}

func (s *Store) antigravityEntry(tx *sql.Tx, event Row, session string, current *string) error {
	at := stamp(event["created_at"])
	step, known := knownCount(event["step_index"])
	if at == "" || !known {
		return nil
	}
	id := antigravityEventID(session, event)
	kind := dataString(event, "type")
	if kind != "USER_INPUT" && kind != "PLANNER_RESPONSE" && kind != "CHECKPOINT" {
		if kind == "GENERIC" && event["source"] == "MODEL" && (event["status"] == "RUNNING" || event["status"] == "DONE") {
			return extendAntigravityActivity(tx, session, *current, at)
		}
		return nil
	}
	if kind == "USER_INPUT" && dataString(event, "source") == "USER_EXPLICIT" {
		if antigravityUserText(dataString(event, "content")) == "" {
			return nil
		}
		*current = session + ":request:" + pythonHash([]any{event["created_at"], step})
	}
	// Only public message content and metering metadata enter this adapter.
	row := Row{}
	for _, field := range []string{"type", "source", "created_at", "step_index", "status", "content", "model", "input_tokens", "cache_read_tokens", "output_tokens", "truncated_fields", "duration_ms", "ttft_ms", "reasoning_effort", "auth_mode", "billing_source", "usage_source"} {
		if value, exists := event[field]; exists {
			row[field] = value
		}
	}
	row["has_tool_calls"] = len(ValueRows(event["tool_calls"])) > 0
	if kind == "CHECKPOINT" {
		delete(row, "content")
	}
	var prior string
	_ = tx.QueryRow("SELECT data FROM antigravity_events WHERE id=?", id).Scan(&prior)
	old := dataRow(prior)
	requestID := firstString(old["request_id"], *current)
	row["request_id"] = requestID
	if row["status"] == "RUNNING" {
		switch dataString(old, "status") {
		case "DONE", "ERROR", "FAILED", "ABORTED", "CANCELLED", "CANCELED":
			row["status"] = old["status"]
		}
	}
	if prior == dataJSON(row) {
		return nil
	}
	if _, err := tx.Exec("INSERT INTO antigravity_events VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", id, session, at, dataJSON(row)); err != nil {
		return err
	}
	var request Row
	if requestID != "" {
		var raw string
		_ = tx.QueryRow("SELECT data FROM antigravity_requests WHERE id=?", requestID).Scan(&raw)
		request = dataRow(raw)
	}
	if kind == "USER_INPUT" && dataString(row, "source") == "USER_EXPLICIT" {
		s.antigravity.activityReconciled = false
		if len(request) == 0 {
			request = Row{"id": *current, "session_id": session, "timestamp": at, "client_kind": antigravityClient, "record_kind": "user_request", "source_ids": []string{"local:antigravity"}, "status": "unknown", "request_status": "unknown"}
		}
		if status := dataString(row, "status"); status == "DONE" || status == "RUNNING" {
			observeAntigravityActivity(request, at, "running")
		}
		request["prompt_preview"] = clip(antigravityUserText(dataString(row, "content")), 600)
		if preview := dataString(request, "prompt_preview"); preview != "" {
			if _, err := tx.Exec("INSERT OR IGNORE INTO usage_session_titles VALUES(?,?)", session, clip(preview, 80)); err != nil {
				return err
			}
		}
		if err := saveMessage(tx, *current, Row{"user": antigravityUserText(dataString(row, "content")), "user_complete": antigravityComplete(row)}); err != nil {
			return err
		}
	}
	if kind == "PLANNER_RESPONSE" {
		if len(request) > 0 && (row["status"] == "RUNNING" || row["status"] == "DONE" && ValueBool(row["has_tool_calls"])) {
			if observeAntigravityActivity(request, at, "running") {
				s.antigravity.activityReconciled = false
			}
		}
		if _, hasInput := knownCount(row["input_tokens"]); hasInput {
			call := Row{"id": id, "session_id": session, "timestamp": at, "model": firstString(row["model"], "unknown"), "client_kind": antigravityClient, "antigravity_request_id": requestID, "record_kind": "model_call", "quality": "response", "status": "completed", "request_status": "completed", "output_tokens": row["output_tokens"], "raw_input_tokens": row["input_tokens"], "raw_cache_read_tokens": row["cache_read_tokens"], "raw_output_tokens": row["output_tokens"], "source_id": "local:antigravity", "output_preview": clip(dataString(row, "content"), 600)}
			for _, field := range []string{"reasoning_effort", "auth_mode", "billing_source", "usage_source"} {
				if value, exists := row[field]; exists {
					call[field] = value
				}
			}
			input, inputOK := knownCount(row["input_tokens"])
			cached, cacheOK := knownCount(row["cache_read_tokens"])
			output, outputOK := knownCount(row["output_tokens"])
			if inputOK && cacheOK && outputOK && input <= math.MaxInt64-cached && input+cached <= math.MaxInt64-output {
				call["input_tokens"], call["cached_input_tokens"], call["total_tokens"] = input+cached, cached, input+cached+output
			}
			for _, field := range []string{"duration_ms", "ttft_ms"} {
				if value, valid := ValueFloat(row[field]); valid && value >= 0 {
					call[field] = value
				}
			}
			if err := s.saveRecord(tx, call, "local:antigravity", "antigravity:app", "1"); err != nil {
				return err
			}
			if len(request) > 0 && dataString(request, "model") == "" {
				request["model"] = row["model"]
			}
		}
		if len(request) > 0 && !ValueBool(row["has_tool_calls"]) && dataString(row, "content") != "" && dataString(row, "status") == "DONE" {
			if observeAntigravityActivity(request, at, "completed") {
				request["output_preview"] = clip(dataString(row, "content"), 600)
				if err := saveMessage(tx, requestID, Row{"final": dataString(row, "content"), "final_complete": antigravityComplete(row), "status": "completed"}); err != nil {
					return err
				}
			}
		}
	}
	if len(request) > 0 {
		switch dataString(row, "status") {
		case "ERROR", "FAILED":
			observeAntigravityActivity(request, at, "failed")
		case "ABORTED", "CANCELLED", "CANCELED":
			observeAntigravityActivity(request, at, "aborted")
		}
		return saveAntigravityRequest(tx, request)
	}
	return nil
}

func extendAntigravityActivity(tx *sql.Tx, session, owner, at string) error {
	if owner == "" {
		return nil
	}
	var raw string
	err := tx.QueryRow("SELECT data FROM antigravity_requests WHERE id=? AND session_id=?", owner, session).Scan(&raw)
	if err == sql.ErrNoRows {
		return nil
	}
	if err != nil {
		return err
	}
	request := dataRow(raw)
	if request["status"] == "running" && at > dataString(request, "running_observed_at") &&
		observeAntigravityActivity(request, at, "running") {
		return saveAntigravityRequest(tx, request)
	}
	return nil
}

func observeAntigravityActivity(request Row, at, status string) bool {
	if at < firstString(request["ended_at"], request["running_observed_at"]) {
		return false
	}
	if status == "running" {
		if ValueBool(request["running_superseded"]) {
			return false
		}
		// Only verified progress after a known end can reopen the same request.
		if request["status"] == "completed" || request["status"] == "failed" || request["status"] == "aborted" {
			if end := dataString(request, "ended_at"); end == "" || at <= end {
				return false
			}
			delete(request, "ended_at")
		}
		request["running_observed_at"] = at
	} else {
		request["ended_at"] = at
	}
	request["status"], request["request_status"] = status, status
	return true
}

func (s *Store) reconcileAntigravityActivity(ctx context.Context) error {
	if s.antigravity.activityReconciled {
		return nil
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// A conversation has one visible active request. The transcript can record
	// multiple accepted inputs for a reused step, with only the newer input
	// receiving the reply. Preserve both rows and any independent terminal state.
	const limit = 64
	rows, err := queryJSON(tx, `SELECT earlier.data FROM antigravity_requests earlier
		WHERE json_extract(earlier.data,'$.status')='running'
		  AND json_extract(earlier.data,'$.running_observed_at')>=?
		  AND EXISTS (
		    SELECT 1 FROM antigravity_requests later
		    JOIN antigravity_events input ON input.id=replace(later.id,':request:',':event:')
		    WHERE later.session_id=earlier.session_id AND later.id<>earlier.id
		      AND json_extract(later.data,'$.timestamp')>=json_extract(earlier.data,'$.timestamp')
		      AND json_extract(input.data,'$.source')='USER_EXPLICIT'
		      AND json_extract(input.data,'$.type')='USER_INPUT'
		      AND json_extract(input.data,'$.status') IN ('DONE','RUNNING')
		      AND (json_extract(later.data,'$.timestamp')>json_extract(earlier.data,'$.timestamp')
		        OR json_extract(input.data,'$.step_index')>(SELECT json_extract(origin.data,'$.step_index')
		          FROM antigravity_events origin WHERE origin.id=replace(earlier.id,':request:',':event:')))
		  ) ORDER BY json_extract(earlier.data,'$.running_observed_at') DESC LIMIT ?`, ledgerStamp(time.Now().Add(-24*time.Hour)), limit)
	if err != nil {
		return err
	}
	for _, row := range rows {
		row["status"], row["request_status"], row["running_superseded"] = "unknown", "unknown", true
		if err := saveAntigravityRequest(tx, row); err != nil {
			return err
		}
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	s.antigravity.activityReconciled = len(rows) < limit
	return nil
}

func presentAntigravityActivity(group Row, now time.Time) {
	if group["status"] != "running" {
		return
	}
	group["duration_running"] = false
	delete(group, "duration_started_at")
	delete(group, "duration_base_ms")
	delete(group, "duration_refresh_at")
	delete(group, "duration_valid_after")
	observed, ok := ParseStamp(group["running_observed_at"])
	if !ok || !now.Before(observed.Add(requestRunningFreshness)) {
		group["status"], group["request_status"] = "unknown", "unknown"
		return
	}
	if observed.After(now) {
		group["status"], group["request_status"] = "unknown", "unknown"
		group["duration_refresh_at"] = unixSeconds(observed)
		return
	}
	group["duration_refresh_at"] = unixSeconds(observed.Add(requestRunningFreshness))
	group["duration_valid_after"] = unixSeconds(observed)
	if start, known := ParseStamp(group["timestamp"]); known && start.Unix() > 0 && !start.After(observed) {
		group["duration_running"] = true
		group["duration_started_at"], group["duration_base_ms"] = UTCStamp(start), 0
	}
}

func saveAntigravityRequest(tx *sql.Tx, request Row) error {
	id, payload := dataString(request, "id"), dataJSON(request)
	var prior string
	_ = tx.QueryRow("SELECT data FROM antigravity_requests WHERE id=?", id).Scan(&prior)
	if payload == prior {
		return nil
	}
	if _, err := tx.Exec("INSERT INTO antigravity_requests VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", id, request["session_id"], payload); err != nil {
		return err
	}
	if _, err := tx.Exec("UPDATE usage_revisions SET revision=revision+1 WHERE kind='ledger'"); err != nil {
		return err
	}
	_, err := tx.Exec("INSERT INTO usage_query_changes(kind,item_id) VALUES('antigravity_request',?)", id)
	return err
}

func (s *Store) rebuildAntigravityGroups(tx *sql.Tx, full bool, now time.Time) error {
	query := "SELECT id,data FROM antigravity_requests"
	if !full {
		query += ` WHERE id IN(SELECT item_id FROM usage_query_changes WHERE kind='antigravity_request'
		UNION SELECT json_extract(data,'$.antigravity_request_id') FROM usage_records WHERE id IN(SELECT item_id FROM usage_query_changes WHERE kind='record') AND json_extract(data,'$.client_kind')='antigravity')`
	}
	last := ""
	for {
		requests, err := queryJSON(tx, "SELECT data FROM ("+query+") WHERE id>? ORDER BY id LIMIT 250", last)
		if err != nil {
			return err
		}
		if len(requests) == 0 {
			break
		}
		for _, group := range requests {
			last = dataString(group, "id")
			presentAntigravityActivity(group, now)
			if _, err := tx.Exec("DELETE FROM usage_request_members WHERE request_id=?", last); err != nil {
				return err
			}
			calls, err := queryJSON(tx, "SELECT data FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')='antigravity' AND json_extract(data,'$.antigravity_request_id')=? ORDER BY timestamp,id", last)
			if err != nil {
				return err
			}
			sum, models := newMetricSum(), map[string]bool{}
			for _, call := range calls {
				sum.add(call)
				models[dataString(call, "model")] = true
				if _, err = tx.Exec("INSERT OR IGNORE INTO usage_request_members VALUES(?,?)", last, call["id"]); err != nil {
					return err
				}
			}
			metrics := sum.row()
			for _, field := range tokenFields {
				group[field] = metrics[field]
			}
			group["total_tokens"], group["cost_usd"] = metrics["tokens"], metrics["usd"]
			group["unpriced_calls"], group["cost_complete"], group["cache_hit_rate"] = metrics["unpriced_calls"], metrics["cost_complete"], metrics["cache_hit_rate"]
			group["call_count"], group["models"] = len(calls), sortedKeys(models)
			group["pricing_status"] = "priced"
			if group["cost_usd"] == nil {
				group["pricing_status"] = "unpriced"
			}
			for _, call := range calls {
				if dataString(call, "pricing_status") == "estimated" && group["cost_usd"] != nil {
					group["pricing_status"] = "estimated"
				}
			}
			if err = saveGroup(tx, group); err != nil {
				return err
			}
		}
	}
	query = `SELECT data FROM usage_priced_calls WHERE json_extract(data,'$.client_kind')='antigravity' AND coalesce(json_extract(data,'$.antigravity_request_id'),'')=''`
	if !full {
		query += " AND id IN(SELECT item_id FROM usage_query_changes WHERE kind='record')"
	}
	orphans, err := queryJSON(tx, query)
	if err != nil {
		return err
	}
	for _, call := range orphans {
		id := dataString(call, "id")
		call["id"], call["record_kind"], call["call_count"] = "unassigned:"+id, "unassigned", 1
		if err = saveGroup(tx, call); err != nil {
			return err
		}
		if _, err = tx.Exec("INSERT OR IGNORE INTO usage_request_members VALUES(?,?)", call["id"], id); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) antigravityDetail(request Row, call bool, page int, contexts ...context.Context) (Row, error) {
	reader := s.readDB(contexts)
	id := dataString(request, "id")
	if call {
		id = dataString(request, "antigravity_request_id")
	}
	raw, _, _, err := readMessage(messageQueryFunc(reader.QueryRow), id)
	if err != nil {
		return nil, err
	}
	result := dataRow(raw)
	if result == nil {
		result = Row{}
	}
	result["request"], result["attachments"], result["images"] = request, []Row{}, []Row{}
	count := 0
	if call {
		result["members"], count = []Row{request}, 1
	} else {
		if err = reader.QueryRow("SELECT count(*) FROM usage_request_members WHERE request_id=?", id).Scan(&count); err != nil {
			return nil, err
		}
		page = min(max(1, page), max(1, (count+49)/50))
		rows, err := reader.Query("SELECT p.data FROM usage_priced_calls p JOIN usage_request_members m ON m.record_id=p.id WHERE m.request_id=? ORDER BY p.timestamp,p.id LIMIT 50 OFFSET ?", id, (page-1)*50)
		if err != nil {
			return nil, err
		}
		members := []Row{}
		for rows.Next() {
			var value string
			if err = rows.Scan(&value); err != nil {
				rows.Close()
				return nil, err
			}
			members = append(members, dataRow(value))
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, err
		}
		result["members"] = members
	}
	result["page"], result["pages"], result["total_members"] = page, max(1, (count+49)/50), count
	visible := append([]Row{request}, ValueRows(result["members"])...)
	if err = s.enrichAntigravityRows(visible, reader); err != nil {
		return nil, err
	}
	if dataString(result, "user") == "" && dataString(result, "final") == "" {
		result["availability"] = "unavailable"
	}
	return result, nil
}
