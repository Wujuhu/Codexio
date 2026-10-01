package backend

import (
	"context"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Mac Database.sourceFiles/recoverMessageDetails, backed by the existing Go
// cursor/origin registry. Recovery is on demand and source-signature gated.
func (s *Store) sourceMessage(request Row) Row {
	if err := s.sourceMessageBatch([]Row{request}); err != nil {
		return nil
	}
	id := turnKey(dataString(request, "session_id"), firstString(request["request_turn_id"], request["turn_id"]))
	var raw string
	_ = s.db.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", id).Scan(&raw)
	value := dataRow(raw)
	final := ValueRows(value["final_attachments"])
	if dataString(value, "final") == "" && len(final) == 0 && (dataString(value, "status") == "completed" || dataString(value, "status") == "aborted") {
		final = ValueRows(value["generated_attachments"])
	}
	value["attachments"] = deduplicatedRequestMedia(append(append([]Row{}, ValueRows(value["attachments"])...), final...))
	return value
}

// Batch all explicit members and the prompt source before assembling details.
// Each indexed file is decoded once even when every requested body is partial.
func (s *Store) sourceMessageBatch(requests []Row) error {
	s.work.Lock()
	defer s.work.Unlock()
	if len(requests) == 0 {
		return nil
	}
	session := dataString(requests[0], "session_id")
	if session == "" {
		return nil
	}
	rows, e := s.db.Query(`SELECT DISTINCT c.key,c.data FROM usage_cursors c WHERE json_extract(c.data,'$.go_state.session_id')=? OR EXISTS(SELECT 1 FROM usage_origins o JOIN usage_turns t ON t.id=o.item_id WHERE o.kind='turn' AND o.file_key=c.key AND json_extract(t.data,'$.session_id')=?) ORDER BY c.key LIMIT 8`, session, session)
	if e != nil {
		return e
	}
	type input struct{ key, path, source, generation, signature string }
	files := []input{}
	seenPaths := map[string]bool{}
	for rows.Next() {
		var key, raw string
		if e = rows.Scan(&key, &raw); e != nil {
			rows.Close()
			return e
		}
		cursor := dataRow(raw)
		path := dataString(cursor, "path")
		if seenPaths[path] || !s.verifiedMessagePath(path) {
			continue
		}
		f, err := os.Open(path)
		if err != nil {
			continue
		}
		identity := ledgerFileIdentity(f)
		f.Close()
		source := "local"
		parts := strings.Split(key, ":")
		if len(parts) == 5 && parts[1] == "local" {
			source = "local:" + parts[2]
		}
		files = append(files, input{key, path, source, dataString(cursor, "generation"), path + ":" + identity + ":" + fileStamp(path)})
		seenPaths[path] = true
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	parts := []string{}
	for _, file := range files {
		parts = append(parts, file.signature)
	}
	fileSignature := pythonHash(parts)
	targets := Row{}
	signatures := map[string][]any{}
	for _, request := range requests {
		if dataString(request, "session_id") != session || approvalRequest(request) || ValueBool(request["is_subagent"]) || dataString(request, "record_kind") == "context_compaction" {
			continue
		}
		turn := firstString(request["request_turn_id"], request["turn_id"])
		if turn == "" {
			continue
		}
		id := turnKey(session, turn)
		var raw string
		_ = s.db.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", id).Scan(&raw)
		stored := dataRow(raw)
		terminal := dataString(request, "status") == "completed" || dataString(request, "status") == "aborted" || dataString(request, "request_status") == "completed" || dataString(request, "request_status") == "aborted"
		root := firstString(request["root_id"], id)
		ownsInput := ValueBool(request["has_user_message"]) || root == id && dataString(request, "prompt_source_turn_id") == ""
		missingMedia := s.media.missingSources(stored)
		var eviction uint64
		if missingMedia {
			eviction = s.media.recoveryGeneration()
		}
		signatureParts := []any{"request-media", requestMediaSchema, fileSignature, ownsInput, terminal, missingMedia, eviction}
		signature := pythonHash(signatureParts)
		complete := len(stored) > 0 && !missingMedia && (!ownsInput || ValueBool(stored["user_complete"]) && ValueInt(stored["user_media_schema"]) == requestMediaSchema) && (!terminal || ValueBool(stored["final_complete"]) && ValueInt(stored["final_media_schema"]) == requestMediaSchema)
		if complete || dataString(stored, "recovery_signature") == signature {
			_, _ = s.db.Exec("UPDATE usage_request_messages SET accessed=? WHERE id=?", float64(time.Now().Unix()), id)
			continue
		}
		targets[id] = true
		signatures[id] = signatureParts
		if len(targets) >= 65 {
			break
		}
	}
	if len(targets) == 0 {
		return nil
	}
	for _, file := range files {
		if _, err := s.recoverSourceMetadata(context.Background(), file.path, file.source, file.key, file.generation, targets, false, 64<<20); err != nil {
			continue
		}
	}
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	defer tx.Rollback()
	for id := range targets {
		var raw string
		_ = tx.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", id).Scan(&raw)
		parts := signatures[id]
		missing := s.media.missingSources(dataRow(raw))
		parts[5], parts[6] = missing, uint64(0)
		if missing {
			parts[6] = s.media.recoveryGeneration()
		}
		// Keep the pre-read source signature so appends still invalidate it,
		// but don't mistake a later cache eviction for an old failed recovery.
		if e = saveMessage(tx, id, Row{"recovery_signature": pythonHash(parts)}); e != nil {
			return e
		}
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	return s.project()
}
func (s *Store) verifiedMessagePath(path string) bool {
	if !filepath.IsAbs(path) || !strings.EqualFold(filepath.Ext(path), ".jsonl") {
		return false
	}
	actual, e := filepath.EvalSymlinks(path)
	if e != nil || !strings.EqualFold(filepath.Clean(actual), filepath.Clean(path)) {
		return false
	}
	st, e := os.Lstat(path)
	if e != nil || !st.Mode().IsRegular() || st.Mode()&os.ModeSymlink != 0 {
		return false
	}
	for _, root := range s.roots() {
		root, e = filepath.Abs(root)
		if e != nil {
			continue
		}
		resolved, err := filepath.EvalSymlinks(root)
		if err != nil || !strings.EqualFold(filepath.Clean(resolved), filepath.Clean(root)) {
			continue
		}
		for _, folder := range []string{"sessions", "archived_sessions"} {
			rel, err := filepath.Rel(filepath.Join(root, folder), actual)
			if err == nil && rel != "." && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel) {
				return true
			}
		}
	}
	return false
}

func (s *Store) requestMessageDetail(request Row, recover bool) (Row, error) {
	session, turn, canonical := ValueString(request["session_id"]), ValueString(request["turn_id"]), ValueString(request["id"])

	db := s.db
	root := request
	var rootRaw string
	if db.QueryRow("SELECT data FROM usage_query_turns WHERE id=?", canonical).Scan(&rootRaw) == nil {
		if value, e := DecodeRow([]byte(rootRaw)); e == nil {
			root = value
		}
	}
	members := []Row{root}
	seen := map[string]bool{canonical: true, turn: true}
	for cursor := 0; cursor < len(members) && len(members) < 64; cursor++ {
		member := members[cursor]
		key, memberTurn := ValueString(member["id"]), ValueString(member["turn_id"])
		// Raw continuation fields can survive a new human input. Only the
		// resolved projection proves membership; legacy rows without it stay out.
		rows, e := db.Query(`SELECT id,data FROM usage_query_turns WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.root_id')=? AND json_extract(data,'$.resolved_id')=id AND coalesce(json_extract(data,'$.has_user_message'),0)=0 AND coalesce(json_extract(data,'$.is_subagent'),0)=0 AND coalesce(json_extract(data,'$.record_kind'),'user_request') NOT IN ('automatic_approval_review','approval_review','context_compaction','subagent_request') AND coalesce(json_extract(data,'$.alias_of'),'')='' AND (json_extract(data,'$.root_turn_id') IN (?,?) OR json_extract(data,'$.continuation_of') IN (?,?)) ORDER BY json_extract(data,'$.started_at'),id LIMIT 65`, session, canonical, key, memberTurn, key, memberTurn)
		if e != nil {
			return nil, e
		}
		for rows.Next() {
			var id, raw string
			if rows.Scan(&id, &raw) != nil {
				continue
			}
			r, e := DecodeRow([]byte(raw))
			if e != nil || seen[id] || dataString(r, "id") != id || approvalRequest(r) {
				continue
			}
			if !seen[ValueString(r["continuation_of"])] && !seen[ValueString(r["root_turn_id"])] {
				continue
			}
			if len(members) == 64 {
				break
			}
			members = append(members, r)
			seen[id] = true
			if member := ValueString(r["turn_id"]); member != "" {
				seen[member] = true

			}
		}
		e = rows.Err()
		rows.Close()
		if e != nil {
			return nil, e
		}
	}
	sort.SliceStable(members, func(i, j int) bool {
		return ValueString(members[i]["started_at"]) < ValueString(members[j]["started_at"])
	})
	promptID := ""
	if !ValueBool(root["has_user_message"]) {
		promptID = ValueString(root["prompt_source_turn_id"])
		if promptID != "" && !strings.HasPrefix(promptID, "turn:") {
			promptID = "turn:" + session + ":" + promptID
		}
	}
	var prompt Row
	if promptID != "" {
		var raw string
		if db.QueryRow(`SELECT data FROM usage_query_turns WHERE id=? AND json_extract(data,'$.session_id')=? AND json_extract(data,'$.root_id')=? AND json_extract(data,'$.resolved_id')=id AND coalesce(json_extract(data,'$.is_subagent'),0)=0 AND coalesce(json_extract(data,'$.record_kind'),'user_request') NOT IN ('automatic_approval_review','approval_review','context_compaction','subagent_request') AND coalesce(json_extract(data,'$.alias_of'),'')=''`, promptID, session, canonical).Scan(&raw) == nil {
			prompt = dataRow(raw)
			if approvalRequest(prompt) || dataString(prompt, "id") != promptID {
				prompt = nil
			}
		}
	}
	if recover {
		targets := append([]Row{}, members...)
		if len(prompt) > 0 {
			targets = append(targets, prompt)
		}
		if e := s.sourceMessageBatch(targets); e != nil {
			return nil, e
		}
		// Recovery can repair ownership and rebuild the projection. Read its
		// current members before assembling any user body, final text or media.
		return s.requestMessageDetail(request, false)
	}
	detail := Row{"user": "", "final": "", "user_complete": false, "final_complete": false, "attachments": []Row{}, "availability": "unavailable"}
	read := func(member Row) Row {
		var raw string
		_ = db.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", dataString(member, "id")).Scan(&raw)
		return dataRow(raw)
	}
	inputs, attachments := []string{}, []Row{}
	userComplete := len(members) < 64
	if len(prompt) > 0 {
		input := read(prompt)
		if dataString(input, "user") != "" || len(ValueRows(input["attachments"])) > 0 {
			inputs = append(inputs, dataString(input, "user"))
			attachments = append(attachments, ValueRows(input["attachments"])...)
			userComplete = userComplete && ValueBool(input["user_complete"]) && ValueInt(input["user_media_schema"]) == requestMediaSchema
		}
	}
	for index, member := range members {
		value := read(member)
		hasInput := dataString(value, "user") != "" || len(ValueRows(value["attachments"])) > 0
		if hasInput {
			if text := dataString(value, "user"); text != "" {
				inputs = append(inputs, text)
			}
			attachments = append(attachments, ValueRows(value["attachments"])...)
		}
		if hasInput || index == 0 && len(prompt) == 0 {
			userComplete = userComplete && ValueBool(value["user_complete"]) && ValueInt(value["user_media_schema"]) == requestMediaSchema
		}
		if index == len(members)-1 {
			finalAttachments := ValueRows(value["final_attachments"])
			terminal := dataString(member, "status") == "completed" || dataString(member, "status") == "aborted"
			generated := dataString(value, "final") == "" && len(finalAttachments) == 0 && terminal
			if generated {
				finalAttachments = ValueRows(value["generated_attachments"])
			}
			attachments = append(attachments, finalAttachments...)
			detail["final"], detail["final_complete"] = value["final"], len(members) < 64 && ValueBool(value["final_complete"]) && ValueInt(value["final_media_schema"]) == requestMediaSchema && (!generated || len(finalAttachments) == 0 || ValueBool(value["generated_complete"]))
		}
	}
	user, fits := requestBoundedText(strings.Join(inputs, "\n\n"), 1<<20)
	attachments = deduplicatedRequestMedia(attachments)
	detail["user"], detail["user_complete"] = user, userComplete && fits && len(attachments) <= requestMediaLimit
	if len(attachments) > requestMediaLimit {
		attachments = attachments[:requestMediaLimit]
		detail["final_complete"] = false
	}
	detail["attachments"] = attachments
	if dataString(detail, "user") != "" || dataString(detail, "final") != "" || len(ValueRows(detail["attachments"])) > 0 {
		detail["availability"] = "partial"
		terminal := len(members) > 0 && (dataString(members[len(members)-1], "status") == "completed" || dataString(members[len(members)-1], "status") == "aborted")
		if len(members) < 64 && ValueBool(detail["user_complete"]) && (!terminal || ValueBool(detail["final_complete"])) {
			detail["availability"] = "available"
		}
	}
	return detail, nil
}
