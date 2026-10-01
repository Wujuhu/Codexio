package backend

import (
	"database/sql"
	"sort"
	"strings"
)

func (s *Store) collectedRequestMessage(content any, user bool, state Row, stamp string) (string, []Row, bool) {
	turn := dataString(state, "turn_id")
	targets := ValueRow(state["_recovery_targets"])
	if ValueBool(state["_metadata_only"]) || len(targets) > 0 && turn != "" && !strings.HasPrefix(turn, "legacy-user:") && !ValueBool(targets[turnKey(dataString(state, "session_id"), turn)]) {
		if user {
			text, attachments, _ := requestUserInput(content)
			return text, attachments, false
		}
		parts := requestMediaContent(content, nil)
		values := make([]any, len(parts))
		for i, part := range parts {
			values[i] = part
		}
		text, attachments, _ := visibleMessage(values, user)
		return text, attachments, false
	}
	created := 0.0
	if date, ok := ParseStamp(stamp); ok {
		created = float64(date.UnixMilli()) / 1000
	}
	text, attachments, complete := s.extractRequestMessage(content, user, firstString(state["cwd"], state["session_cwd"]), created)
	return text, attachments, complete && (len(attachments) == 0 || created > 0)
}

func requestFinalFingerprint(text string, attachments []Row) string {
	if text != "" {
		return pythonHash([]string{text})
	}
	keys := []string{}
	for _, attachment := range attachments {
		keys = append(keys, dataString(attachment, "source_key"))
	}
	return pythonHash(keys)
}

// Only outputs of observed image-tool calls, including composed exec calls,
// become generated attachments. Arbitrary tool text is never interpreted as a
// filesystem capability. The mapping is bounded and tied to its exact turn.
func (s *Store) collectRequestImageTool(tx *sql.Tx, payload, state Row, stamp string) error {
	kind := dataString(payload, "type")
	callID := firstString(payload["call_id"], payload["id"])
	tools := ValueRow(state["request_image_tools"])
	name := dataString(payload, "name")
	arguments, _ := requestBoundedText(firstString(payload["arguments"], payload["input"]), 65536)
	composed := (name == "exec" || name == "functions.exec") && (strings.Contains(arguments, "tools.image_gen__imagegen(") || strings.Contains(arguments, "image_gen.imagegen("))
	if (kind == "function_call" || kind == "custom_tool_call") && (requestImageTool(name) || composed) && callID != "" && len(callID) <= 512 {
		tools[callID] = state["turn_id"]
		if len(tools) > 16 {
			keys := []string{}
			for key := range tools {
				if key != callID {
					keys = append(keys, key)
				}
			}
			sort.Strings(keys)
			for _, key := range keys {
				delete(tools, key)
				if len(tools) <= 16 {
					break
				}
			}
		}
	}
	matched := (kind == "function_call_output" || kind == "custom_tool_call_output") && dataString(tools, callID) == dataString(state, "turn_id")
	if kind == "image_generation_call" || matched {
		output := payload["output"]
		if kind == "image_generation_call" {
			output = payload
		}
		_, attachments, complete := s.collectedRequestMessage(requestImageToolParts(output), false, state, stamp)
		images := []Row{}
		for _, attachment := range attachments {
			if strings.HasPrefix(dataString(attachment, "mime"), "image/") {
				images = append(images, attachment)
			}
		}
		if len(images) > 0 {
			if err := persistCollectedMessage(tx, turnKey(dataString(state, "session_id"), dataString(state, "turn_id")), Row{"generated_attachments": images, "generated_complete": complete, "generated_media_schema": requestMediaSchema}, state); err != nil {
				return err
			}
			ValueRow(ValueRow(state["turns"])[dataString(state, "turn_id")])["has_generated_image"] = true
		}
		delete(tools, callID)
	}
	state["request_image_tools"] = tools
	return nil
}
