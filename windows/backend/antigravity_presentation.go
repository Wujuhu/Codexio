package backend

import (
	"math"
	"regexp"
	"strings"
	"time"
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

type antigravityRequestTiming struct {
	row          Row
	count, timed int
	output       int64
	duration     float64
	incomplete   bool
}

func (g *antigravityRequestTiming) addDuration(value float64) {
	if value > 0 {
		g.duration += value
		g.timed++
	}
}

func (g *antigravityRequestTiming) apply() {
	count, countKnown := knownCount(g.row["call_count"])
	output, outputKnown := knownCount(g.row["output_tokens"])
	if g.incomplete || g.count == 0 || g.count != g.timed || !countKnown || count != int64(g.count) ||
		!outputKnown || output != g.output || math.IsInf(g.duration, 0) {
		return
	}
	g.row["duration_ms"], g.row["duration_source"] = g.duration, "antigravity_model_calls"
}

func collectAntigravityRequestTimings(records []Row, targets map[string][]nativeTimingTarget, reader queryReader) (map[string]*antigravityRequestTiming, error) {
	groups := map[string]*antigravityRequestTiming{}
	args := []any{}
	now := time.Now()
	for _, row := range records {
		if row["client_kind"] != antigravityClient || row["record_kind"] != "user_request" {
			continue
		}
		presentAntigravityActivity(row, now)
		if id := dataString(row, "id"); id != "" && row["status"] == "completed" && groups[id] == nil {
			groups[id] = &antigravityRequestTiming{row: row}
			args = append(args, id)
		}
	}
	if len(args) == 0 {
		return groups, nil
	}
	slots := strings.TrimSuffix(strings.Repeat("?,", len(args)), ",")
	// Stream only timing metadata for the complete member set of visible requests.
	// The detail page limit applies to displayed rows, not to this aggregate.
	rows, err := reader.Query(`
		SELECT m.request_id,m.record_id,coalesce(p.session_id,''),
		       coalesce(p.data->>'$.antigravity_request_id',''),
		       coalesce(p.data->>'$.client_kind',''),coalesce(p.data->>'$.record_kind',''),
		       coalesce(p.data->>'$.status',''),coalesce(e.session_id,''),
		       coalesce(e.data->>'$.request_id',''),coalesce(e.data->>'$.type',''),
		       coalesce(e.data->>'$.status',''),coalesce(e.data->>'$.created_at',''),
		       e.data->>'$.step_index',
		       CASE WHEN json_type(p.data,'$.output_tokens') IN ('integer','real','text') THEN p.data->>'$.output_tokens' END,
		       CASE WHEN json_type(p.data,'$.duration_ms') IN ('integer','real') THEN p.data->>'$.duration_ms' END,
		       p.data->>'$.raw_input_tokens',p.data->>'$.raw_cache_read_tokens',p.data->>'$.raw_output_tokens'
		  FROM usage_request_members m
		  LEFT JOIN usage_priced_calls p ON p.id=m.record_id
		  LEFT JOIN antigravity_events e ON e.id=m.record_id
		 WHERE m.request_id IN (`+slots+`) ORDER BY m.request_id,m.record_id`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var owner, id, callSession, callRequest, client, kind, callStatus string
		var eventSession, eventRequest, eventType, eventStatus, original string
		var step, output, duration, input, cached, rawOutput any
		if err = rows.Scan(&owner, &id, &callSession, &callRequest, &client, &kind, &callStatus,
			&eventSession, &eventRequest, &eventType, &eventStatus, &original,
			&step, &output, &duration, &input, &cached, &rawOutput); err != nil {
			return nil, err
		}
		group := groups[owner]
		group.count++
		session := dataString(group.row, "session_id")
		tokens, known := knownCount(output)
		if group.incomplete || session == "" || callSession != session || eventSession != session ||
			callRequest != owner || eventRequest != owner || client != antigravityClient || kind != "model_call" ||
			callStatus != "completed" || eventType != "PLANNER_RESPONSE" || eventStatus != "DONE" ||
			!known || tokens > math.MaxInt64-group.output {
			group.incomplete = true
			continue
		}
		group.output += tokens
		if value, exists := ValueFloat(duration); exists && value >= 0 {
			group.addDuration(value)
			continue
		}
		if target, ok := makeNativeTimingTarget(id, session, original, step, input, cached, rawOutput); ok {
			target.owner = owner
			targets[session] = append(targets[session], target)
		} else {
			group.incomplete = true
		}
	}
	return groups, rows.Err()
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
