package backend

// Like readTitles/enrichUpstream, this reads Codex's SQLite source in read-only
// mode. The indexed submission precedes rollout context/message initialization.
// It is request evidence, never the current thread title or mutable preferences.
import (
	"database/sql"
	"path/filepath"
	"strconv"
	"strings"
)

// Split Rust Debug values only at the outer level. Quoted prompt text must not
// be able to supply model/settings fields or a different submission identity.
func submissionParts(value string) ([]string, bool) {
	parts := []string{}
	stack := []byte{}
	quoted, escaped, start := false, false, 0
	for i := 0; i < len(value); i++ {
		c := value[i]
		if quoted {
			if escaped {
				escaped = false
			} else if c == '\\' {
				escaped = true
			} else if c == '"' {
				quoted = false
			}
			continue
		}
		switch c {
		case '"':
			quoted = true
		case '{', '[', '(':
			stack = append(stack, c)
			if len(stack) > 64 {
				return nil, false
			}
		case '}', ']', ')':
			if len(stack) == 0 || !strings.Contains("{} [] ()", string([]byte{stack[len(stack)-1], c})) {
				return nil, false
			}
			stack = stack[:len(stack)-1]
		case ',':
			if len(stack) == 0 {
				parts = append(parts, strings.TrimSpace(value[start:i]))
				start = i + 1
			}
		}
	}
	if quoted || len(stack) != 0 {
		return nil, false
	}
	if tail := strings.TrimSpace(value[start:]); tail != "" {
		parts = append(parts, tail)
	}
	return parts, true
}

func submissionFields(value, name string) map[string]string {
	value = strings.TrimSpace(value)
	prefix := name + " {"
	if !strings.HasPrefix(value, prefix) || !strings.HasSuffix(value, "}") {
		return nil
	}
	parts, ok := submissionParts(value[len(prefix) : len(value)-1])
	if !ok {
		return nil
	}
	fields := map[string]string{}
	for _, part := range parts {
		key, raw, found := strings.Cut(part, ":")
		if !found {
			return nil
		}
		fields[strings.TrimSpace(key)] = strings.TrimSpace(raw)
	}
	return fields
}

func submissionOption(value string) string {
	for strings.HasPrefix(value, "Some(") && strings.HasSuffix(value, ")") {
		value = strings.TrimSpace(value[5 : len(value)-1])
	}
	return value
}

func submissionString(value string) (string, bool) {
	value = submissionOption(value)
	if len(value) < 2 || value[0] != '"' || value[len(value)-1] != '"' {
		return "", false
	}
	// Rust Debug uses \u{...} and \0; preserve escaped backslashes literally.
	var normalized strings.Builder
	for i := 0; i < len(value); i++ {
		if value[i] == '\\' && i+1 < len(value) {
			if strings.HasPrefix(value[i:], `\u{`) {
				end := strings.IndexByte(value[i+3:], '}')
				if end < 0 {
					return "", false
				}
				code, err := strconv.ParseUint(value[i+3:i+3+end], 16, 32)
				if err != nil || code > 0x10ffff || code >= 0xd800 && code <= 0xdfff {
					return "", false
				}
				normalized.WriteString(`\U` + strings.Repeat("0", 8-len(strconv.FormatUint(code, 16))) + strconv.FormatUint(code, 16))
				i += end + 3
				continue
			}
			if value[i+1] == '0' {
				normalized.WriteString(`\x00`)
			} else {
				normalized.WriteByte(value[i])
				normalized.WriteByte(value[i+1])
			}
			i++
			continue
		}
		normalized.WriteByte(value[i])
	}
	decoded, err := strconv.Unquote(normalized.String())
	return decoded, err == nil
}

func parseRequestSubmission(body, turn string) (Row, []any) {
	_, raw, found := strings.Cut(body, "Submission sub=")
	if !found {
		return nil, nil
	}
	submission := submissionFields(raw, "Submission")
	id, ok := submissionString(submission["id"])
	if !ok || id != turn {
		return nil, nil
	}
	op := submissionFields(submission["op"], "TurnInput")
	request := submissionFields(op["request"], "TurnInputRequest")
	input := submissionFields(request["input"], "UserInput")
	settings := submissionFields(request["thread_settings"], "ThreadSettingsOverrides")
	if input == nil || settings == nil {
		return nil, nil
	}
	snapshot := Row{"version": 1, "input_state": "unavailable"}
	mode := submissionFields(submissionOption(settings["collaboration_mode"]), "CollaborationMode")
	collaboration := submissionFields(mode["settings"], "Settings")
	if model, ok := submissionString(settings["model"]); ok && model != "" {
		snapshot["model"] = model
	} else if model, ok := submissionString(collaboration["model"]); ok && model != "" {
		snapshot["model"] = model
	}
	for _, raw := range []string{settings["effort"], collaboration["reasoning_effort"]} {
		effort := strings.ToLower(submissionOption(raw))
		if strings.Contains("|none|minimal|low|medium|high|xhigh|max|ultra|", "|"+effort+"|") && raw != "None" {
			snapshot["reasoning_effort"] = effort
			break
		}
	}
	if tier, ok := submissionString(settings["service_tier"]); ok && requestSpeed(tier) != "unknown" {
		snapshot["service_tier"] = requestSpeed(tier)
	} else if strings.HasPrefix(settings["service_tier"], "Some(") && submissionOption(settings["service_tier"]) == "None" {
		// An explicit cleared override cannot preserve a prior Fast selection.
		snapshot["service_tier"] = nil
	}
	content := input["content"]
	if !strings.HasPrefix(content, "[") || !strings.HasSuffix(content, "]") {
		return snapshot, nil
	}
	items, ok := submissionParts(content[1 : len(content)-1])
	if !ok || len(items) > 256 {
		return snapshot, nil
	}
	if len(items) == 0 {
		snapshot["input_state"] = "empty"
		return snapshot, nil
	}
	parts := []any{}
	for _, item := range items {
		fields := submissionFields(item, "Text")
		if text, ok := submissionString(fields["text"]); ok {
			parts = append(parts, Row{"type": "input_text", "text": text})
		} else {
			snapshot["input_partial"] = true
		}
	}
	if len(parts) > 0 {
		snapshot["input_state"] = "available"
	}
	return snapshot, parts
}

func (s *Store) requestSubmission(source, session, turn, timestamp string) (Row, []any) {
	started, ok := ParseStamp(timestamp)
	if !ok || session == "" || turn == "" || s.options.Mock {
		return nil, nil
	}
	for index, root := range s.roots() {
		root = filepath.Clean(root)
		id := "local"
		if index > 0 {
			id += ":" + HashString(strings.ToLower(root))[:16]
		}
		if source != id {
			continue
		}
		paths, _ := filepath.Glob(filepath.Join(root, "logs_*.sqlite"))
		for _, path := range paths {
			db, err := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?mode=ro&_pragma=busy_timeout(100)")
			if err != nil {
				continue
			}
			// thread_id + ts uses Codex's existing index, not a full log scan.
			rows, err := db.Query(`SELECT feedback_log_body FROM logs WHERE thread_id=? AND ts>=? AND ts<=? AND target='codex_core::session::handlers' AND length(feedback_log_body)<=2097152 ORDER BY ts DESC,ts_nanos DESC,id DESC LIMIT 64`, session, started.Unix()-60, started.Unix()+2)
			var snapshot Row
			var input []any
			if err == nil {
				for rows.Next() {
					var body string
					if rows.Scan(&body) == nil {
						snapshot, input = parseRequestSubmission(body, turn)
						if snapshot != nil {
							break
						}
					}
				}
				rows.Close()
			}
			db.Close()
			if snapshot != nil {
				return snapshot, input
			}
		}
	}
	return nil, nil
}
