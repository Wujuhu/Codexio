package backend

import (
	"bufio"
	"context"
	"database/sql"
	"io"
	"os"
	"strings"
	"time"
)

// Mac v0.3.5 RequestResume: explicit switches and interrupted execution preserve
// uninterrupted input ownership. A new human message always ends that chain.
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
		text, attachments, _ := requestUserInput(content)
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
	if kind == "compacted" || kind == "event_msg" && event == "item_completed" && dataString(ValueRow(p["item"]), "type") == "ContextCompaction" {
		current["maintenance"] = true
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
	previous, input := ValueRow(context["previous"]), ValueRow(context["input"])
	modelSwitch := kind == "response_item" && dataString(p, "role") == "developer" && marked && owner != "" && owner == id
	work := kind == "response_item" && (dataString(p, "role") == "assistant" && (event == "" || event == "message") || event == "function_call" || event == "custom_tool_call")
	interruptedEnd := kind == "event_msg" && event == "turn_aborted" && dataString(p, "reason") == "interrupted"
	retry := dataString(previous, "status") == "aborted" && dataString(previous, "reason") == "interrupted" && (work || interruptedEnd)
	if (!modelSwitch && !retry) || ValueBool(current["has_user"]) || ValueBool(current["resumed"]) || !modelSwitch && ValueBool(current["maintenance"]) {
		return nil
	}
	source, preview := dataString(input, "turn"), dataString(input, "preview")
	if source == "" || preview == "" || dataString(previous, "source") != source || dataString(previous, "id") == id || source == id {
		return nil
	}
	current["source"], current["resumed"] = source, true
	context["current"] = current
	resumeKind := "interrupted"
	if modelSwitch {
		resumeKind = "model_switch"
	}
	return Row{"turn": id, "source": source, "preview": preview, "kind": resumeKind, "continuation": previous["id"]}
}
func contextOnlyUser(entry Row) bool {
	p := ValueRow(entry["payload"])
	if dataString(entry, "type") != "response_item" || dataString(p, "role") != "user" {
		return false
	}
	markers := ValueStrings(ValueRow(p["internal_chat_message_metadata_passthrough"])["content_item_kinds"])
	if len(markers) == 0 {
		return false
	}
	for _, marker := range markers {
		if !strings.HasPrefix(marker, "additional_content.") && marker != "agents_md.instructions" && !strings.HasPrefix(marker, "environments.") {
			return false
		}
	}
	return true
}
func entryUserContent(entry Row) (any, string) {
	if contextOnlyUser(entry) {
		return nil, ""
	}
	p := ValueRow(entry["payload"])
	kind, sub := dataString(entry, "type"), dataString(p, "type")
	content := func(raw any, metadata Row) any {
		parts := requestMediaContent(raw, metadata)
		if len(parts) == 0 {
			return nil
		}
		// Keep the collector's existing []any representation so pure-text
		// fingerprints and its side-effect-free ownership preview stay stable.
		values := make([]any, len(parts))
		for i, part := range parts {
			values[i] = part
		}
		if text, attachments, _ := requestUserInput(values); text == "" && len(attachments) == 0 {
			// Replies still reach the existing question-owner path, but internal
			// environment/instruction envelopes are not new human input.
			rawText, _ := requestMediaText(parts)
			if !strings.Contains(rawText, "<send_user_message_question_reply>") {
				return nil
			}
		}
		return values
	}
	if kind == "response_item" && dataString(p, "role") == "user" && (sub == "" || sub == "message") {
		return content(p["content"], p), "response"
	}
	if kind == "event_msg" && sub == "user_message" {
		if p["message"] != nil {
			return content(p["message"], p), "event"
		}
		return content(p["content"], p), "event"
	}
	item := ValueRow(p["item"])
	if kind == "event_msg" && strings.EqualFold(strings.ReplaceAll(dataString(item, "type"), "_", ""), "usermessage") {
		if item["content"] != nil {
			return content(item["content"], item), "item"
		}
		return content(item["message"], item), "item"
	}
	return nil, ""
}
func requestMessagePreview(text string, attachments []Row) string {
	// The preview describes the human's prose, not an image URL or file wrapper.
	// requestImageMatches deliberately leaves fenced and inline code untouched.
	matches := requestImageMatches(text)
	for i := len(matches) - 1; i >= 0; i-- {
		match := matches[i]
		text = text[:match.start] + " " + text[match.end:]
	}
	text = strings.TrimSpace(text)
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
	// Keep the previous pure-text identity byte-for-byte. Typed image-only
	// inputs need their sources in the identity so separate human inputs cannot
	// alias the old empty-text hash. An old cursor is accepted once at ingestion.
	text, _, _ := visibleMessage(content, true)
	if text != "" {
		return messageLegacyFingerprint(content)
	}
	images := []any{}
	for _, part := range requestMediaContent(content, nil) {
		if requestImagePart(part) {
			images = append(images, requestImagePartIdentity(part))
		}
	}
	if len(images) > 0 {
		return pythonHash([]any{"request-images-v1", images})
	}
	return messageLegacyFingerprint(content)
}
func messageLegacyFingerprint(content any) string {
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
		if !automaticResume(row) {
			return
		}
		if ValueBool(row["model_switch_continuation"]) || ValueBool(row["request_resume_continuation"]) {
			row["continuation_of"] = nil
		}
		row["resume_kind"], row["prompt_source_turn_id"], row["model_switch_continuation"], row["request_resume_continuation"] = nil, nil, false, false
	} else {
		if dataString(row, "prompt_preview") != "" && !automaticResume(row) {
			return
		}
		row["prompt_preview"], row["prompt_source_turn_id"], row["resume_kind"] = patch["preview"], patch["source"], firstString(patch["kind"], "model_switch")
		if predecessor := dataString(patch, "continuation"); predecessor != "" && dataString(row, "continuation_of") == "" {
			row["continuation_of"] = turnKey(dataString(state, "session_id"), predecessor)
			row["request_resume_continuation"] = true
		}
	}
}
func automaticResume(row Row) bool {
	kind := dataString(row, "resume_kind")
	return kind == "model_switch" || kind == "interrupted"
}
func remapResumeTurn(state Row, oldID, newID string) {
	context := ValueRow(state["resume_tracking"])
	input := ValueRow(context["input"])
	if dataString(input, "turn") == oldID {
		input["turn"] = newID
	}
	for _, name := range []string{"current", "previous"} {
		segment := ValueRow(context[name])
		for _, field := range []string{"id", "source"} {
			if dataString(segment, field) == oldID {
				segment[field] = newID
			}
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
		repairCachedInputOwnership(tx.QueryRow, item.row)
		row := mergeRecoveredTurn(item.row, evidence)
		if _, e = tx.Exec("UPDATE usage_turns SET data=? WHERE id=? AND data<>?", dataJSON(row), item.id, dataJSON(row)); e != nil {
			return e
		}
	}
	return nil
}
func repairCachedInputOwnership(query func(string, ...any) *sql.Row, row Row) {
	if !strings.Contains(strings.ToLower(dataString(row, "prompt_preview")), "<external_codex_apps_") {
		return
	}
	var raw string
	e := query("SELECT data FROM usage_request_messages WHERE id=?", dataString(row, "id")).Scan(&raw)
	if e == nil || e == sql.ErrNoRows {
		repairInputOwnership(row, dataRow(raw))
	}
}
func mergeRecoveredTurn(row, evidence Row) Row {
	row = CloneRow(row)
	// Recover source timing without replacing a terminal row with an earlier
	// running prefix of the bounded replay. No wall-clock timestamp is inferred.
	if ValueBool(evidence["explicit_task_start"]) && evidence["started_inferred"] == false {
		if _, ok := ParseStamp(evidence["started_at"]); ok {
			row["started_at"], row["started_inferred"] = evidence["started_at"], false
		}
	}
	if end, ok := ParseStamp(evidence["ended_at"]); ok && (dataString(evidence, "status") == "completed" || dataString(evidence, "status") == "aborted") {
		previous, exists := ParseStamp(row["ended_at"])
		if !exists || !end.Before(previous) {
			row["ended_at"], row["status"] = evidence["ended_at"], evidence["status"]
			if duration, ok := ValueFloat(evidence["duration_ms"]); ok && duration >= 0 {
				row["duration_ms"] = duration
			}
		}
	}
	if observed, ok := ParseStamp(evidence["observed_at"]); ok {
		previous, exists := ParseStamp(row["observed_at"])
		if !exists || observed.After(previous) {
			row["observed_at"] = evidence["observed_at"]
		}
	}
	contextHashes := map[string]bool{}
	for _, hash := range ValueStrings(evidence["context_input_hashes"]) {
		if hash != pythonHash("") {
			contextHashes[hash] = true
		}
	}
	oldHashes := ValueStrings(row["input_hashes"])
	controlOnly := !ValueBool(evidence["has_user_message"]) && len(oldHashes) > 0
	for _, hash := range oldHashes {
		controlOnly = controlOnly && contextHashes[hash]
	}
	if controlOnly {
		row["prompt_preview"], row["has_user_message"], row["input_hashes"] = "", false, nil
		row["input_ownership_version"] = requestMetadataVersion
	}
	// Replace only a proved control prompt or the tracker's inherited preview.
	// A retained genuine prompt is not disproved by an empty recovery body.
	if ValueBool(evidence["has_user_message"]) && (ValueInt(row["input_ownership_version"]) == requestMetadataVersion || automaticResume(row)) {
		row["prompt_preview"] = evidence["prompt_preview"]
	}
	// A complete replay can disprove settings formerly copied from notifications
	// after the turn ended. Preserve unknowns rather than retaining that false data.
	if ValueInt(evidence["request_settings_version"]) == 1 && dataString(evidence, "ended_at") != "" {
		for _, key := range []string{"model", "reasoning_effort", "service_tier", "request_settings_version"} {
			row[key] = evidence[key]
		}
	}
	for _, k := range []string{"cwd", "session_cwd", "root_turn_id", "parent_session_id", "parent_turn_id", "inherited_parent_turn_id", "alias_of", "model", "reasoning_effort", "service_tier", "model_context_window"} {
		if evidence[k] != nil && evidence[k] != "" && evidence[k] != "unknown" {
			row[k] = evidence[k]
		}
	}
	for _, k := range []string{"has_user_message", "has_final_message", "context_compaction_observed", "explicit_task_start", "context_compaction_completed", "is_approval_review", "is_subagent", "parent_turn_explicit"} {
		if ValueBool(evidence[k]) {
			row[k] = true
		}
	}
	if ValueBool(evidence["has_user_message"]) && evidence["input_hashes"] != nil {
		row["input_hashes"] = evidence["input_hashes"]
	}
	for _, k := range []string{"prompt_preview", "output_preview"} {
		if dataString(row, k) == "" && dataString(evidence, k) != "" {
			row[k] = evidence[k]
		}
	}
	for _, k := range []string{"prompt_source_turn_id", "resume_kind", "continuation_of", "model_switch_continuation", "request_resume_continuation"} {
		if v, ok := evidence[k]; ok {
			row[k] = v
		}
	}
	row["record_kind"] = classifyRequest(row)
	return row
}

// Reuse the collector's metadata state machine, never its meters, cursors or
// agent-link writes. Each source recovery has a fixed byte and row-state bound.
func (s *Store) recoverSourceMetadata(ctx context.Context, path, source, file, generation string, targets Row, metadataOnly bool, limit int64) (Row, error) {
	state := newMetadataRecoveryState(path, targets, metadataOnly)
	return s.recoverSourceMetadataRange(ctx, path, source, file, generation, state, 0, limit, 0)
}
func newMetadataRecoveryState(path string, targets Row, metadataOnly bool) Row {
	return Row{"session_id": rolloutID(path), "rollout_id": rolloutID(path), "model": "unknown", "provider": "unknown", "turns": Row{}, "_recovery": true, "_metadata_only": metadataOnly, "_recovery_targets": targets}
}

const metadataRepairByteBudget = 32 << 20
const metadataRepairTimeBudget = 500 * time.Millisecond

// The ordinary byte cursor and meter state remain authoritative. A separate
// bounded metadata cursor survives collections until it reaches that boundary.
func (s *Store) repairSourceMetadataCursor(ctx context.Context, path, source, file string, cursor Row) error {
	progress := ValueRow(cursor["metadata_repair"])
	if ValueInt(progress["version"]) != requestMetadataVersion {
		progress = Row{}
	}
	oldState := ValueRow(cursor["go_state"])
	through := ValueInt(cursor["offset"])
	if len(progress) == 0 {
		var needed int
		if e := s.db.QueryRow(`SELECT EXISTS(SELECT 1 FROM usage_turns WHERE json_extract(data,'$.session_id')=?)`, firstString(oldState["session_id"], rolloutID(path))).Scan(&needed); e != nil {
			return e
		}
		if needed == 0 {
			cursor["metadata_version"], cursor["metadata_partial"] = requestMetadataVersion, false
			delete(cursor, "metadata_repair")
			_, e := s.db.Exec("UPDATE usage_cursors SET data=? WHERE key=?", dataJSON(cursor), file)
			return e
		}
		progress = Row{"version": requestMetadataVersion, "through": through, "state": newMetadataRecoveryState(path, nil, true)}
	}
	through = min(through, ValueInt(progress["through"]))
	state := ValueRow(progress["state"])
	start := ValueInt(state["_recovery_offset"])
	recovered, e := s.recoverSourceMetadataRange(ctx, path, source, file, dataString(cursor, "generation"), state, start, through, metadataRepairTimeBudget)
	if e != nil {
		return e
	}
	if recovered["request_source"] != nil {
		oldState["request_source"] = recovered["request_source"]
	}
	for id, value := range ValueRow(recovered["turns"]) {
		if existing := ValueRow(ValueRow(oldState["turns"])[id]); len(existing) > 0 {
			repairCachedInputOwnership(s.db.QueryRow, existing)
			ValueRow(oldState["turns"])[id] = mergeRecoveredTurn(existing, ValueRow(value))
		}
	}
	complete := ValueInt(recovered["_recovery_offset"]) >= through
	if complete {
		cursor["metadata_version"] = requestMetadataVersion
		delete(cursor, "metadata_repair")
		if through == ValueInt(cursor["offset"]) {
			for _, key := range []string{"resume_tracking", "last_input", "previous_turn_id", "request_model", "pending_request_settings", "model", "reasoning_effort", "service_tier"} {
				oldState[key] = recovered[key]
			}
		}
	} else {
		progress["state"] = recovered
		cursor["metadata_repair"] = progress
	}
	cursor["go_state"] = oldState
	cursor["metadata_partial"] = !complete || ValueBool(recovered["_recovery_skipped"])
	_, e = s.db.Exec("UPDATE usage_cursors SET data=? WHERE key=?", dataJSON(cursor), file)
	return e
}

func (s *Store) recoverSourceMetadataRange(ctx context.Context, path, source, file, generation string, state Row, start, through int64, budget time.Duration) (Row, error) {
	f, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer f.Close()
	if _, e = f.Seek(start, io.SeekStart); e != nil {
		return nil, e
	}
	limit := max(int64(0), through-start)
	if budget > 0 {
		limit = min(limit, metadataRepairByteBudget)
	}
	reader := bufio.NewReaderSize(io.LimitReader(f, limit), 65536)
	tx, e := s.db.Begin()
	if e != nil {
		return nil, e
	}
	defer tx.Rollback()
	if ValueBool(state["_metadata_only"]) && start == 0 {
		if e = s.repairRetainedInputs(tx, dataString(state, "session_id")); e != nil {
			return nil, e
		}
	}
	offset := start
	started := time.Now()
	for {
		if e = ctx.Err(); e != nil {
			return nil, e
		}
		if budget > 0 && offset > start && time.Since(started) >= budget {
			break
		}
		line, err := readBoundedLine(reader, rolloutLineLimit)
		if err == io.EOF {
			// Retry an ordinary split line from its exact start next collection.
			// A single oversized payload must not stall every later turn: discard
			// it in bounded chunks and forget continuity across the unknown input.
			if budget > 0 && len(line) > 0 && (offset == start || ValueBool(state["_recovery_skip_line"])) {
				offset += int64(len(line))
				state["_recovery_skip_line"], state["_recovery_skipped"] = true, true
				delete(state, "resume_tracking")
				delete(state, "turn_id")
				delete(state, "last_input")
			}
			break
		}
		if err != nil {
			return nil, err
		}
		lineStart := offset
		offset += int64(len(line))
		if ValueBool(state["_recovery_skip_line"]) {
			delete(state, "_recovery_skip_line")
			continue
		}
		entry, err := DecodeRow(line)
		if err != nil {
			continue
		}
		entry["_byte_offset"] = lineStart
		kind := dataString(entry, "type")
		p := ValueRow(entry["payload"])
		sub := dataString(p, "type")
		if kind == "token_usage_record" || sub == "token_count" || sub == "raw_response_completed" {
			// Preserve message-representation boundaries without decoding or
			// persisting any token counter, call, price or source contribution.
			state["usage_since_input"] = true
			continue
		}
		if e = s.processEntry(tx, entry, state, source, file, generation); e != nil {
			return nil, e
		}
	}
	state["_recovery_offset"] = offset
	if e = tx.Commit(); e != nil {
		return nil, e
	}
	return state, nil
}
