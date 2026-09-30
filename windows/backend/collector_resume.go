package backend

import (
	"bufio"
	"context"
	"database/sql"
	"io"
	"os"
	"strings"
)

// Mac v0.3.4 Core.RequestClassification and RequestResume: only an explicit
// model-switch marker with uninterrupted input ownership can resume a request.
func approvalRequest(row Row) bool {
	kind := dataString(row, "record_kind")
	return kind == "automatic_approval_review" || kind == "approval_review" || ValueBool(row["is_approval_review"]) || strings.EqualFold(dataString(row, "model"), "codex-auto-review")
}
func approvalSource(value any) bool {
	name := func(v any) bool {
		switch strings.ToLower(ValueString(v)) {
		case "guardian", "approval_review", "auto_review", "codex-auto-review":
			return true
		}
		return false
	}
	if name(value) {
		return true
	}
	source := ValueRow(value)
	if name(source["subagent"]) {
		return true
	}
	agent := ValueRow(source["subagent"])
	if name(agent["other"]) {
		return true
	} // retained Windows/Python source alias
	for _, key := range []string{"guardian", "approval_review", "auto_review"} {
		if source[key] != nil || agent[key] != nil {
			return true
		}
	}
	return false
}
func classifyRequest(row Row) string {
	if approvalRequest(row) {
		return "automatic_approval_review"
	}
	turn, root := dataString(row, "turn_id"), dataString(row, "root_turn_id")
	if ValueBool(row["has_user_message"]) || dataString(row, "continuation_of") != "" || root != "" && root != turn && root != dataString(row, "id") {
		return "user_request"
	}
	if ValueBool(row["context_compaction_observed"]) && ValueBool(row["explicit_task_start"]) && ValueBool(row["context_compaction_completed"]) && dataString(row, "prompt_preview") == "" && dataString(row, "resume_kind") == "" && !ValueBool(row["has_final_message"]) && !ValueBool(row["is_subagent"]) {
		return "context_compaction"
	}
	if ValueBool(row["is_subagent"]) {
		return "subagent_request"
	}
	return "context_message" // retain accounting with unknown input ownership
}
func resumeObserve(entry, state Row) Row {
	context := ValueRow(state["resume_tracking"])
	state["resume_tracking"] = context
	p := ValueRow(entry["payload"])
	kind, event := dataString(entry, "type"), dataString(p, "type")
	if kind == "session_meta" {
		return nil
	}
	if ValueBool(ValueRow(state["request_source"])["is_subagent"]) || inheritedEntry(entry, state) {
		return nil
	}
	current := ValueRow(context["current"])
	id := dataString(state, "turn_id")
	if id != "" && dataString(current, "id") != id {
		context["previous"] = current
		current = Row{"id": id}
	}
	metadata := ValueRow(p["internal_chat_message_metadata_passthrough"])
	thread := firstString(p["thread_id"], metadata["thread_id"])
	if thread != "" && thread != dataString(state, "session_id") {
		return nil
	}
	owner := firstString(p["turn_id"], metadata["turn_id"])
	if owner != "" && owner != dataString(current, "id") {
		return nil
	}
	content, _ := entryUserContent(entry)
	if content != nil {
		text, attachments, _ := visibleMessage(content, true)
		if (text != "" || len(attachments) > 0) && dataString(current, "id") != "" {
			resumed := ValueBool(current["resumed"])
			current["source"], current["has_user"], current["resumed"] = id, true, false
			context["input"] = Row{"turn": id, "preview": requestMessagePreview(text, attachments)}
			context["current"] = current
			if resumed {
				return Row{"turn": id, "clear": true}
			}
			return nil
		}
	}
	if kind == "event_msg" && (event == "task_complete" || event == "turn_complete" || event == "turn_aborted") {
		current["status"] = "completed"
		if event == "turn_aborted" {
			current["status"] = "aborted"
		}
		current["reason"] = p["reason"]
	}
	context["current"] = current
	marked := false
	for _, marker := range ValueStrings(metadata["content_item_kinds"]) {
		if marker == "model_switch.instructions" {
			marked = true
		}
	}
	if kind != "response_item" || dataString(p, "role") != "developer" || !marked || ValueBool(current["has_user"]) || owner == "" || owner != id {
		return nil
	}
	previous, input := ValueRow(context["previous"]), ValueRow(context["input"])
	source, preview := dataString(input, "turn"), dataString(input, "preview")
	if source == "" || preview == "" || dataString(previous, "source") != source || dataString(previous, "id") == id || source == id {
		return nil
	}
	current["source"], current["resumed"] = source, true
	patch := Row{"turn": id, "source": source, "preview": preview}
	if dataString(previous, "status") == "aborted" && dataString(previous, "reason") == "interrupted" {
		patch["continuation"] = previous["id"]
	}
	return patch
}
func entryUserContent(entry Row) (any, string) {
	p := ValueRow(entry["payload"])
	kind, sub := dataString(entry, "type"), dataString(p, "type")
	if kind == "response_item" && dataString(p, "role") == "user" && (sub == "" || sub == "message") {
		return p["content"], "response"
	}
	if kind == "event_msg" && sub == "user_message" {
		if p["message"] != nil {
			return p["message"], "event"
		}
		return p["content"], "event"
	}
	item := ValueRow(p["item"])
	if kind == "event_msg" && strings.EqualFold(strings.ReplaceAll(dataString(item, "type"), "_", ""), "usermessage") {
		if item["content"] != nil {
			return item["content"], "item"
		}
		return item["message"], "item"
	}
	return nil, ""
}
func requestMessagePreview(text string, attachments []Row) string {
	if text == "" && len(attachments) > 0 {
		return "附件消息"
	}
	return clip(strings.Join(strings.Fields(text), " "), 600)
}

// Only disproven control-only input is reversible. Empty/evicted human bodies
// and explicit continuation/review evidence must retain their existing ownership.
func completeControlInput(text string) bool {
	matches := externalAppInputPattern.FindAllStringSubmatchIndex(text, -1)
	if len(matches) == 0 {
		return false
	}
	end := 0
	for _, match := range matches {
		if strings.TrimSpace(text[end:match[0]]) != "" || !strings.EqualFold(text[match[2]:match[3]], text[match[4]:match[5]]) {
			return false
		}
		end = match[1]
	}
	return strings.TrimSpace(text[end:]) == ""
}

func repairInputOwnership(row, detail Row) bool {
	preview := dataString(row, "prompt_preview")
	// Ingestion rejects incomplete control prefixes, but a clipped historical
	// preview cannot prove that genuine input did not follow the envelope.
	if !completeControlInput(preview) && (!ValueBool(detail["user_complete"]) || !completeControlInput(dataString(detail, "user"))) {
		return false
	}
	text := userText(dataString(detail, "user"))
	attachments := ValueRows(detail["attachments"])
	row["prompt_preview"] = requestMessagePreview(text, attachments)
	row["has_user_message"] = text != "" || len(attachments) > 0
	row["input_hashes"] = nil
	row["input_ownership_version"] = requestMetadataVersion
	row["record_kind"] = classifyRequest(row)
	return true
}

func (s *Store) repairRetainedInputs(tx *sql.Tx, session string) error {
	rows, e := tx.Query(`SELECT id,data FROM usage_turns WHERE json_extract(data,'$.session_id')=? AND instr(lower(COALESCE(json_extract(data,'$.prompt_preview'),'')),'<external_codex_apps_')>0 ORDER BY json_extract(data,'$.started_at') DESC LIMIT 512`, session)
	if e != nil {
		return e
	}
	type retained struct {
		id  string
		row Row
	}
	items := []retained{}
	for rows.Next() {
		var id, raw string
		if e = rows.Scan(&id, &raw); e != nil {
			rows.Close()
			return e
		}
		items = append(items, retained{id, dataRow(raw)})
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, item := range items {
		messageID := dataString(item.row, "id")
		var raw string
		_ = tx.QueryRow("SELECT data FROM usage_request_messages WHERE id=?", messageID).Scan(&raw)
		detail := dataRow(raw)
		if !repairInputOwnership(item.row, detail) {
			continue
		}
		if dataString(detail, "user") != "" {
			text := userText(dataString(detail, "user"))
			if e = saveMessage(tx, messageID, Row{"user": text, "user_complete": ValueBool(detail["user_complete"]) || text == ""}); e != nil {
				return e
			}
			var digest string
			var revision int64
			if tx.QueryRow("SELECT digest,revision FROM usage_request_messages WHERE id=?", messageID).Scan(&digest, &revision) == nil {
				item.row["message_digest"], item.row["message_revision"] = digest, revision
			}
		}
		payload := dataJSON(item.row)
		if _, e = tx.Exec("UPDATE usage_turns SET data=? WHERE id=? AND data<>?", payload, item.id, payload); e != nil {
			return e
		}
	}
	return nil
}
func messageFingerprint(content any) string {
	text := ValueString(content)
	if parts, ok := content.([]any); ok {
		texts := []string{}
		for _, part := range parts {
			row := ValueRow(part)
			kind := dataString(row, "type")
			if kind == "text" || kind == "input_text" || kind == "output_text" {
				texts = append(texts, dataString(row, "text"))
			}
		}
		text = strings.Join(texts, "\n")
	}
	return pythonHash(clip(text, 262144))
}
func applyResumePatch(patch, row, state Row) {
	if dataString(patch, "turn") != dataString(row, "turn_id") {
		return
	}
	if ValueBool(patch["clear"]) {
		if dataString(row, "resume_kind") != "model_switch" {
			return
		}
		if ValueBool(row["model_switch_continuation"]) {
			row["continuation_of"] = nil
		}
		row["resume_kind"], row["prompt_source_turn_id"], row["model_switch_continuation"] = nil, nil, false
	} else {
		if dataString(row, "prompt_preview") != "" && dataString(row, "resume_kind") != "model_switch" {
			return
		}
		row["prompt_preview"], row["prompt_source_turn_id"], row["resume_kind"] = patch["preview"], patch["source"], "model_switch"
		if predecessor := dataString(patch, "continuation"); predecessor != "" && dataString(row, "continuation_of") == "" {
			row["continuation_of"] = turnKey(dataString(state, "session_id"), predecessor)
			row["model_switch_continuation"] = true
		}
	}
}
func persistCollectedMessage(tx *sql.Tx, id string, patch, state Row) error {
	if ValueBool(state["_metadata_only"]) {
		return nil
	}
	if targets := ValueRow(state["_recovery_targets"]); len(targets) > 0 && !ValueBool(targets[id]) {
		// Preserve a pre-context synthetic input for its later official alias.
		if strings.Contains(id, ":legacy-user:") && patch["user"] != nil {
			state["_pending_message"] = Row{"id": id, "patch": patch}
		}
		return nil
	}
	row := ValueRow(ValueRow(state["turns"])[dataString(state, "turn_id")])
	patch["status"], patch["started_at"], patch["completed_at"] = row["status"], row["started_at"], row["ended_at"]
	return saveMessage(tx, id, patch)
}
func persistCollectedTurn(tx *sql.Tx, evidence, state Row, source, file, generation string) error {
	if !ValueBool(state["_recovery"]) {
		return saveMetadata(tx, "usage_turns", "turn", evidence, source, file, generation)
	}
	if targets := ValueRow(state["_recovery_targets"]); len(targets) > 0 && !ValueBool(targets[dataString(evidence, "id")]) {
		return nil
	}
	rows, e := tx.Query("SELECT id,data FROM usage_turns WHERE json_extract(data,'$.session_id')=? AND json_extract(data,'$.turn_id')=?", evidence["session_id"], evidence["turn_id"])
	if e != nil {
		return e
	}
	type stored struct {
		id  string
		row Row
	}
	items := []stored{}
	for rows.Next() {
		var id, raw string
		if e = rows.Scan(&id, &raw); e != nil {
			rows.Close()
			return e
		}
		items = append(items, stored{id, dataRow(raw)})
	}
	rows.Close()
	for _, item := range items {
		row := item.row
		// A repaired preview may be replaced by a later genuine input observed
		// during this bounded recovery, even when the old preview was nonempty.
		if ValueInt(row["input_ownership_version"]) == requestMetadataVersion && ValueBool(evidence["has_user_message"]) {
			row["prompt_preview"] = evidence["prompt_preview"]
		}
		for _, k := range []string{"cwd", "session_cwd", "root_turn_id", "parent_session_id", "parent_turn_id", "inherited_parent_turn_id", "alias_of", "model", "reasoning_effort", "service_tier", "model_context_window"} {
			if evidence[k] != nil && evidence[k] != "" && evidence[k] != "unknown" {
				row[k] = evidence[k]
			}
		}
		for _, k := range []string{"has_user_message", "has_final_message", "context_compaction_observed", "explicit_task_start", "context_compaction_completed", "is_approval_review", "is_subagent"} {
			if ValueBool(evidence[k]) {
				row[k] = true
			}
		}
		for _, k := range []string{"prompt_preview", "output_preview"} {
			if dataString(row, k) == "" && dataString(evidence, k) != "" {
				row[k] = evidence[k]
			}
		}
		// Metadata generated by this tracker is reversible when a genuine user
		// message disproves a provisional model-switch continuation.
		for _, k := range []string{"prompt_source_turn_id", "resume_kind", "continuation_of", "model_switch_continuation"} {
			if v, ok := evidence[k]; ok {
				row[k] = v
			}
		}
		row["record_kind"] = classifyRequest(row)
		if _, e = tx.Exec("UPDATE usage_turns SET data=? WHERE id=? AND data<>?", dataJSON(row), item.id, dataJSON(row)); e != nil {
			return e
		}
	}
	return nil
}

// Reuse the collector's metadata state machine, never its meters, cursors or
// agent-link writes. Each source recovery has a fixed byte and row-state bound.
func (s *Store) recoverSourceMetadata(ctx context.Context, path, source, file, generation string, targets Row, metadataOnly bool, limit int64) (Row, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	state := Row{"session_id": rolloutID(path), "rollout_id": rolloutID(path), "model": "unknown", "provider": "unknown", "turns": Row{}, "_recovery": true, "_metadata_only": metadataOnly, "_recovery_targets": targets}
	reader := bufio.NewReaderSize(io.LimitReader(f, limit), 65536)
	tx, e := s.db.Begin()
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	if metadataOnly {
		if e = s.repairRetainedInputs(tx, dataString(state, "session_id")); e != nil {
			return nil, e
		}
	}
	offset := int64(0)
	for {
		if e = ctx.Err(); e != nil {
			return nil, e
		}
		line, err := readBoundedLine(reader, 8<<20)
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		start := offset
		offset += int64(len(line))
		entry, err := DecodeRow(line)
		if err != nil {
			continue
		}
		entry["_byte_offset"] = start
		kind := dataString(entry, "type")
		p := ValueRow(entry["payload"])
		sub := dataString(p, "type")
		if kind == "token_usage_record" || sub == "token_count" || sub == "raw_response_completed" {
			continue
		}
		if e = s.processEntry(tx, entry, state, source, file, generation); e != nil {
			return nil, e
		}
	}
	if e = tx.Commit(); e != nil {
		return nil, e
	}
	return state, nil
}
