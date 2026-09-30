package backend

import (
	"bufio"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// Read only messages belonging to the verified main request and explicit main
// continuations. Preview strings and intermediate/tool/system output are never
// substitutes for source text. Rollout recovery is on demand, one 64 MiB file.
func (m *MobileHost) sourceDetail(request Row, recover bool) (Row, error) {
	session, turn, canonical := ValueString(request["session_id"]), ValueString(request["turn_id"]), ValueString(request["id"])
	turns := map[string]bool{turn: true}
	ids := []string{canonical}
	db := m.store.Database()
	rows, e := db.Query(`SELECT id,data FROM usage_query_turns WHERE json_extract(data,'$.session_id')=? AND coalesce(json_extract(data,'$.is_subagent'),0)=0 AND coalesce(json_extract(data,'$.record_kind'),'user_request') NOT IN ('automatic_approval_review','context_compaction','subagent_request') AND (json_extract(data,'$.root_id')=? OR json_extract(data,'$.root_turn_id') IN (?,?) OR json_extract(data,'$.continuation_of') IN (?,?)) ORDER BY json_extract(data,'$.started_at'),id LIMIT 64`, session, canonical, canonical, turn, canonical, turn)
	if e != nil {
		return nil, e
	}
	for rows.Next() {
		var id, raw string
		if rows.Scan(&id, &raw) != nil {
			continue
		}
		r, _ := DecodeRow([]byte(raw))
		member := ValueString(r["turn_id"])
		if member != "" && (id == canonical || ValueString(r["continuation_of"]) != "") {
			turns[member] = true
			if id != canonical {
				ids = append(ids, id)
			}
		}
	}
	rows.Close()
	detail := Row{"user": "", "final": "", "user_complete": false, "final_complete": false, "attachments": []Row{}, "availability": "source"}
	for _, id := range ids {
		var raw string
		if db.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", id).Scan(&raw) != nil {
			continue
		}
		r, _ := DecodeRow([]byte(raw))
		if ValueString(detail["user"]) == "" && ValueString(r["user"]) != "" {
			detail["user"] = r["user"]
			detail["user_complete"] = r["user_complete"]
			detail["attachments"] = r["attachments"]
		}
		if ValueString(r["final"]) != "" {
			detail["final"] = r["final"]
			detail["final_complete"] = r["final_complete"]
		}
	}
	missingImagePath := false
	for _, a := range ValueRows(detail["attachments"]) {
		if strings.HasPrefix(ValueString(a["mime"]), "image/") && ValueString(a["path"]) == "" {
			missingImagePath = true
		}
	}
	if recover && (missingImagePath || !ValueBool(detail["user_complete"]) || ValueString(request["request_status"]) == "completed" && !ValueBool(detail["final_complete"])) {
		if file := m.registeredRollout(session, canonical); file != "" {
			source := mobileRecover(file, session, turns)
			for _, field := range []string{"user", "final"} {
				if text := ValueString(source[field]); text != "" && len(text) > len(ValueString(detail[field])) {
					detail[field] = text
					detail[field+"_complete"] = source[field+"_complete"]
				}
			}
			if a := ValueRows(source["attachments"]); len(a) > 0 {
				detail["attachments"] = a
			}
		}
	}
	return detail, nil
}
func (m *MobileHost) registeredRollout(session, canonical string) string {
	if !mobileUUID.MatchString(session) {
		return ""
	}
	rows, e := m.store.Database().Query(`SELECT DISTINCT json_extract(c.data,'$.path') FROM usage_cursors c WHERE c.key LIKE ? OR EXISTS(SELECT 1 FROM usage_origins o WHERE o.file_key=c.key AND o.kind='turn' AND o.item_id=?) LIMIT 8`, "usage:local:%:"+strings.ToLower(session), canonical)
	if e != nil {
		return ""
	}
	defer rows.Close()
	roots := ValueStrings(m.config()["codex_roots"])
	for rows.Next() {
		var path string
		if rows.Scan(&path) != nil || !filepath.IsAbs(path) || !strings.EqualFold(filepath.Ext(path), ".jsonl") {
			continue
		}
		st, e := os.Lstat(path)
		if e != nil || !st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0 || st.Size() > 64<<20 {
			continue
		}
		actual, e := filepath.EvalSymlinks(path)
		if e != nil || !strings.EqualFold(filepath.Clean(path), filepath.Clean(actual)) {
			continue
		}
		for _, root := range roots {
			root, e = filepath.Abs(root)
			if e != nil {
				continue
			}
			root, e = filepath.EvalSymlinks(root)
			if e != nil {
				continue
			}
			for _, dir := range []string{"sessions", "archived_sessions"} {
				rel, e := filepath.Rel(filepath.Join(root, dir), actual)
				if e == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel) {
					return actual
				}
			}
		}
	}
	return ""
}
func mobileRecover(path, session string, turns map[string]bool) Row {
	f, e := os.Open(path)
	if e != nil {
		return nil
	}
	defer f.Close()
	st, e := f.Stat()
	if e != nil || st.Size() > 64<<20 {
		return nil
	}
	reader := bufio.NewReaderSize(io.LimitReader(f, 64<<20), 65536)
	result := Row{"user": "", "final": "", "user_complete": false, "final_complete": false, "attachments": []Row{}}
	current := ""
	owner := ""
	var pending any
	for {
		line := []byte{}
		oversized := false
		var readError error
		for {
			part, e := reader.ReadSlice('\n')
			if !oversized {
				if len(line)+len(part) > 2<<20 {
					line = nil
					oversized = true
				} else {
					line = append(line, part...)
				}
			}
			if e != bufio.ErrBufferFull {
				readError = e
				break
			}
		}
		if !oversized && len(line) > 0 {
			entry, e := DecodeRow(line)
			if e == nil {
				p := ValueRow(entry["payload"])
				kind, sub := ValueString(entry["type"]), ValueString(p["type"])
				if kind == "session_meta" {
					owner = ValueString(p["id"])
					if owner == "" {
						owner = ValueString(p["thread_id"])
					}
					if owner != session {
						return nil
					}
				}
				if kind == "turn_context" || kind == "event_msg" && (sub == "task_started" || sub == "turn_started") {
					next := ValueString(p["turn_id"])
					if next == "" {
						next = ValueString(p["id"])
					}
					if next != "" {
						current = next
					}
					if owner == session && turns[current] && pending != nil {
						mobileRecoverUser(result, pending)
					}
					pending = nil
				}
				member := ValueString(p["turn_id"])
				if member == "" {
					member = current
				}
				input := any(nil)
				if kind == "event_msg" && sub == "user_message" {
					input = p["message"]
					if input == nil {
						input = p["content"]
					}
				} else if kind == "response_item" && ValueString(p["role"]) == "user" {
					input = p["content"]
				}
				if input != nil {
					if owner == session && turns[member] {
						mobileRecoverUser(result, input)
					} else {
						pending = input
					}
				}
				if owner == session && turns[member] {
					var final any
					if kind == "event_msg" && (sub == "task_complete" || sub == "turn_complete") {
						final = p["last_agent_message"]
					} else if kind == "response_item" && ValueString(p["role"]) == "assistant" && (ValueString(p["phase"]) == "final" || ValueString(p["phase"]) == "final_answer" || ValueString(p["channel"]) == "final") {
						final = p["content"]
					}
					if final != nil {
						text, _, complete := mobileVisibleMessage(final, false)
						if text != "" {
							result["final"] = text
							result["final_complete"] = complete
						}
					}
				}
			}
		}
		if readError != nil {
			if !errors.Is(readError, io.EOF) {
				return nil
			}
			break
		}
	}
	return result
}
func mobileRecoverUser(result Row, input any) {
	text, a, complete := mobileVisibleMessage(input, true)
	if len(text) > len(ValueString(result["user"])) {
		result["user"] = text
		result["user_complete"] = complete
		result["attachments"] = a
	}
	if len(ValueRows(result["attachments"])) == 0 && len(a) > 0 {
		result["attachments"] = a
	}
}

// Shared parsing supplies the established My request extraction and final-only
// text semantics. Local attachment paths remain internal until a bounded JPEG
// thumbnail is prepared; no path or original file bytes reach an envelope.
func mobileVisibleMessage(value any, user bool) (string, []Row, bool) {
	text, _, complete := visibleMessage(value, user)
	attachments := []Row{}
	items, ok := value.([]any)
	if !ok {
		items = []any{value}
	}
	for _, item := range items {
		r := ValueRow(item)
		kind := strings.ToLower(strings.ReplaceAll(ValueString(r["type"]), "_", ""))
		switch kind {
		case "image", "inputimage", "localimage", "imageurl", "file", "inputfile":
			path := ValueString(r["path"])
			if len(path) > 4096 {
				path = ""
			}
			name := ValueString(r["name"])
			if name == "" {
				name = path
			}
			name = filepath.Base(strings.ReplaceAll(name, "\\", "/"))
			if name == "." || name == "" {
				name = "附件"
			}
			name = mobilePrefix(name, 240)
			mime := "application/octet-stream"
			if strings.Contains(kind, "image") {
				mime = "image/*"
			}
			attachments = append(attachments, Row{"id": HashString(kind + name)[:32], "name": name, "mime": mime, "path": path})
			if len(attachments) >= 6 {
				return text, attachments, complete
			}
		}
	}
	return text, attachments, complete
}
