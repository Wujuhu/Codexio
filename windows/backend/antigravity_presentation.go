package backend

import (
	"regexp"
	"strings"
)

var antigravityVersionedModel = regexp.MustCompile(`(?i)^gemini[-_ ]+(\d+(?:\.\d+)?)[-_ ]+(hydrogen|carbon|neon|argon|pro|flash(?:[-_ ]+lite)?)(?:[-_ ]+(.+))?$`)

// These are derived labels for a bounded query response. The model ID and
// request-time metadata in the ledger remain the original source evidence.
func antigravityModelPresentation(row Row) {
	raw := firstString(row["model_display_name"], row["model"])
	label := requestModelName(raw)
	effort := antigravityDisplayEffort(dataString(row, "reasoning_effort"))
	if parts := antigravityVersionedModel.FindStringSubmatch(strings.TrimSpace(raw)); len(parts) > 0 {
		base := "Gemini " + parts[1] + " " + antigravityTitleWords(parts[2])
		tail := strings.ToLower(strings.TrimSpace(parts[3]))
		thinking := tail
		explicitMarker := false
		for _, prefix := range []string{"thinking-", "thinking_", "thinking ", "reasoning-", "reasoning_", "reasoning "} {
			if strings.HasPrefix(thinking, prefix) {
				thinking = strings.TrimPrefix(thinking, prefix)
				explicitMarker = true
				break
			}
		}
		parsed := antigravityDisplayEffort(thinking)
		if tail == "" {
			label = base
		} else if parsed != "" && (explicitMarker || parsed != "Max" && parsed != "Ultra") {
			label = base
			if effort == "" {
				effort = parsed
			}
		} else if tail == "max" || tail == "ultra" {
			label = base + " " + antigravityTitleWords(tail)
		}
	}
	row["model_label"], row["display_reasoning_effort"] = label, effort
}

func antigravityDisplayEffort(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "extra":
		return requestEffortName("xhigh")
	case "none", "minimal", "low", "medium", "high", "xhigh", "extra-high", "extra_high", "extra high", "max", "ultra":
		return requestEffortName(value)
	default:
		return ""
	}
}

func antigravityTitleWords(value string) string {
	words := strings.FieldsFunc(strings.ToLower(value), func(r rune) bool { return r == '-' || r == '_' || r == ' ' })
	for i, word := range words {
		words[i] = strings.ToUpper(word[:1]) + word[1:]
	}
	return strings.Join(words, " ")
}

func (s *Store) enrichAntigravityRows(records []Row, reader queryReader) error {
	if err := s.enrichAntigravityTimings(records, reader); err != nil {
		return err
	}
	requests := map[string]bool{}
	for _, row := range records {
		if dataString(row, "client_kind") != antigravityClient {
			continue
		}
		antigravityModelPresentation(row)
		if id := dataString(row, "antigravity_request_id"); id != "" && id != dataString(row, "id") {
			requests[id] = true
		}
	}
	ids := sortedKeys(requests)
	if len(ids) == 0 {
		return nil
	}
	args := make([]any, len(ids))
	for i, id := range ids {
		args[i] = id
	}
	slots := strings.TrimSuffix(strings.Repeat("?,", len(ids)), ",")
	rows, err := reader.Query("SELECT id,data FROM antigravity_requests WHERE id IN ("+slots+")", args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	previews := map[string]string{}
	for rows.Next() {
		var id, raw string
		if err = rows.Scan(&id, &raw); err != nil {
			return err
		}
		previews[id] = dataString(dataRow(raw), "prompt_preview")
	}
	if err = rows.Err(); err != nil {
		return err
	}
	for _, row := range records {
		if dataString(row, "client_kind") == antigravityClient {
			if preview, found := previews[dataString(row, "antigravity_request_id")]; found {
				row["prompt_preview"] = preview
			}
		}
	}
	return nil
}
