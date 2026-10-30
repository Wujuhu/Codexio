package backend

import (
	"math"
	"sort"
	"strings"
	"time"
)

const requestDurationProjectionVersion = 2
const requestRunningFreshness = 15 * time.Minute

// Read only timing attached to the call; never borrow a containing turn's
// duration or infer TTFT from the time of a complete assistant message.
func projectRecordedCallTiming(row Row) {
	id := dataString(row, "id")
	if dataString(row, "response_id") == "" && !strings.HasPrefix(id, "response:") && !strings.HasPrefix(id, "modern:") && !strings.HasPrefix(id, "legacy:") {
		return
	}
	// Only explicit call metadata is eligible. Never search message content,
	// thinkingDurationMs, item timestamps or a containing turn's duration.
	for _, source := range recordedTimingSources(row) {
		if current, known := ValueFloat(row["duration_ms"]); !known || current < 0 {
			if value, ok := ValueFloat(source["duration_ms"]); ok && value >= 0 {
				row["duration_ms"] = value
			}
		}
	}
	ms, known := ValueFloat(row["duration_ms"])
	known = known && ms >= 0 && !math.IsInf(ms, 0) && !math.IsNaN(ms)
	if !known {
		delete(row, "duration_ms")
	}
	end, ended := ParseStamp(row["call_ended_at"])
	if !known {
		if start, ok := ParseStamp(row["call_started_at"]); ok && ended && !end.Before(start) {
			ms = end.Sub(start).Seconds() * 1000
			row["duration_ms"] = ms
			row["duration_source"] = "call_bounds"
			known = true
		}
	}
	// Responses uses Unix seconds. A response lifecycle can provide average
	// output TPS without establishing TTFT or requiring stream interception.
	if !known && dataString(row, "client_kind") != "claude_code" && (dataString(row, "status") == "" || dataString(row, "status") == "completed") {
		start, startOK := ValueFloat(row["created_at"])
		end, endOK := ValueFloat(row["completed_at"])
		_, startBool := row["created_at"].(bool)
		_, endBool := row["completed_at"].(bool)
		if startOK && endOK && !startBool && !endBool && !math.IsNaN(start) && !math.IsNaN(end) && !math.IsInf(start, 0) && !math.IsInf(end, 0) && start >= 0 && end >= start && end < 253402300800 {
			ms = (end - start) * 1000
			known = true
			row["duration_ms"], row["duration_source"] = ms, "response_lifecycle"
			first, _ := ParseStamp(start)
			last, _ := ParseStamp(end)
			row["call_started_at"], row["call_ended_at"] = UTCStamp(first), UTCStamp(last)
		}
	}
	if known && dataString(row, "duration_source") == "" {
		row["duration_source"] = "recorded_call"
	}
	if ttft, ok := recordedTTFTMilliseconds(row); ok {
		row["ttft_ms"] = ttft
	} else {
		delete(row, "ttft_ms")
	}
	if ValueBool(row["call_duration_conflict"]) {
		delete(row, "duration_ms")
		known = false
	}
	if ValueBool(row["call_ttft_conflict"]) {
		delete(row, "ttft_ms")
	}
	quality := strings.Split(dataString(row, "quality"), ":")[0]
	metered := quality == "response" || quality == "legacy_last" || quality == "cumulative_delta" && strings.HasPrefix(id, "response:")
	terminal := dataString(row, "client_kind") != "claude_code" || ValueBool(row["usage_terminal"]) || ValueBool(row["terminal_usage"])
	if metered && terminal && dataString(row, "status") == "" && !ValueBool(row["duration_running"]) && (known || ended) {
		row["status"] = "completed"
	}
}

func recordedTimingSources(row Row) []Row {
	result := []Row{row}
	for _, key := range []string{"timing", "metrics"} {
		source := ValueRow(row[key])
		if response := dataString(source, "response_id"); response != "" && response != dataString(row, "response_id") {
			continue
		}
		result = append(result, source)
	}
	return result
}

// A completed output item does not establish first-token latency.
func recordedTTFTMilliseconds(row Row) (float64, bool) {
	if ValueBool(row["call_ttft_conflict"]) {
		return 0, false
	}
	for _, source := range recordedTimingSources(row) {
		for _, key := range []string{"ttft_ms", "time_to_first_token_ms", "ttft_seconds", "time_to_first_token_seconds"} {
			if _, boolean := source[key].(bool); boolean {
				continue
			}
			value, ok := ValueFloat(source[key])
			if key == "ttft_seconds" || key == "time_to_first_token_seconds" {
				value *= 1000
			}
			if ok && value >= 0 && !math.IsNaN(value) && !math.IsInf(value, 0) {
				return value, true
			}
		}
	}
	return 0, false
}

var recordedCallFields = []string{"duration_ms", "duration_source", "call_started_at", "call_ended_at", "created_at", "completed_at", "ttft_ms", "call_duration_conflict", "call_ttft_conflict"}

// The caller has already established this response's transcript identity. A
// nested API object must independently match it; IDs are never joined by time.
func attachedCallTiming(source Row, response, client string) Row {
	if response == "" || dataString(source, "response_id") != "" && dataString(source, "response_id") != response {
		return Row{}
	}
	r := Row{}
	for _, key := range append(append([]string{}, recordedCallFields...), "started_at", "status", "timing", "metrics") {
		if source[key] != nil {
			r[key] = source[key]
		}
	}
	r["response_id"], r["client_kind"], r["quality"] = response, client, "response"
	for target, alias := range map[string]string{"call_started_at": "started_at", "call_ended_at": "completed_at"} {
		if r[target] == nil {
			if stamp, ok := ParseStamp(source[alias]); ok && stamp.Unix() >= 0 && stamp.Unix() < 253402300800 {
				r[target] = UTCStamp(stamp)
			}
		}
	}
	projectRecordedCallTiming(r)
	result := Row{}
	for _, key := range recordedCallFields {
		if r[key] != nil {
			result[key] = r[key]
		}
	}
	if nested := ValueRow(source["response"]); dataString(nested, "id") == response {
		result = mergeRecordedCallTiming(result, attachedCallTiming(nested, response, client))
	}
	return result
}

// Usage fragments may arrive separately from timing. Retain compatible facts
// independently; conflicting durations/first-token measurements stay missing.
func mergeRecordedCallTiming(old, next Row) Row {
	result := Row{}
	for _, key := range recordedCallFields {
		if old[key] != nil {
			result[key] = old[key]
		}
		if next[key] != nil {
			result[key] = next[key]
		}
	}
	for _, pair := range [][2]string{{"duration_ms", "call_duration_conflict"}, {"ttft_ms", "call_ttft_conflict"}} {
		a, aOK := ValueFloat(old[pair[0]])
		b, bOK := ValueFloat(next[pair[0]])
		if ValueBool(old[pair[1]]) || ValueBool(next[pair[1]]) || aOK && bOK && a != b {
			result[pair[1]] = true
			delete(result, pair[0])
		}
	}
	return result
}

type requestExecutionInterval struct {
	start, end time.Time
}

// Match macOS Analytics.interval: an explicit execution start takes precedence
// over a reported duration. Inferred input/context timestamps are not starts.
func requestCompletedInterval(row Row) (requestExecutionInterval, bool) {
	status := dataString(row, "status")
	end, ended := ParseStamp(row["ended_at"])
	if !ended || status != "completed" && status != "aborted" {
		return requestExecutionInterval{}, false
	}
	if start, ok := ParseStamp(row["started_at"]); ok && row["started_inferred"] == false && !end.Before(start) {
		return requestExecutionInterval{start, end}, true
	}
	if ms, ok := ValueFloat(row["duration_ms"]); ok && ms >= 0 && ms < float64(math.MaxInt64)/float64(time.Millisecond) {
		return requestExecutionInterval{end.Add(-time.Duration(ms * float64(time.Millisecond))), end}, true
	}
	return requestExecutionInterval{}, false
}

// Intersect completed execution with the time before the current open segment,
// then merge it. A child overlapping that segment contributes only its earlier
// portion; parallel children and duplicate representations never add wall time.
func mergedRequestMilliseconds(intervals []requestExecutionInterval, before *time.Time) float64 {
	merged := make([]requestExecutionInterval, 0, len(intervals))
	for _, interval := range intervals {
		if before != nil {
			if !interval.start.Before(*before) {
				continue
			}
			if interval.end.After(*before) {
				interval.end = *before
			}
		}
		merged = append(merged, interval)
	}
	if len(merged) == 0 {
		return 0
	}
	sort.Slice(merged, func(i, j int) bool { return merged[i].start.Before(merged[j].start) })
	current, seconds := merged[0], 0.0
	for _, next := range merged[1:] {
		if !next.start.After(current.end) {
			if next.end.After(current.end) {
				current.end = next.end
			}
		} else {
			seconds += current.end.Sub(current.start).Seconds()
			current = next
		}
	}
	return (seconds + current.end.Sub(current.start).Seconds()) * 1000
}

func projectRequestDuration(group Row, members []Row, now time.Time) {
	// These fields are a stable projection of source evidence. Never persist a
	// duration calculated from the projection, app, page or refresh start time.
	group["duration_ms"], group["duration_base_ms"], group["duration_completed_ms"] = nil, nil, nil
	group["duration_started_at"], group["duration_running"] = nil, false
	group["duration_active_starts"] = []string{}
	group["duration_refresh_at"], group["duration_valid_after"] = nil, nil
	group["completed_at"], group["ended_at"] = nil, nil
	boundary := func(at time.Time) {
		seconds := float64(at.Unix()) + float64(at.Nanosecond())/1e9
		field := "duration_valid_after"
		if at.After(now) {
			field = "duration_refresh_at"
		}
		previous, exists := ValueFloat(group[field])
		if !exists || field == "duration_refresh_at" && seconds < previous || field == "duration_valid_after" && seconds > previous {
			group[field] = seconds
		}
	}
	intervals := []requestExecutionInterval{}
	var activeStart, latestEnd time.Time
	var finalContinuation Row
	segments, running := 0, false
	known, terminal := true, true
	for _, meta := range members {
		// macOS Analytics.statusMembers includes associated agents, while an
		// automatic approval review has its own lifecycle and cannot keep a
		// human request running or lengthen its execution duration.
		if !approvalRequest(group) && approvalRequest(meta) {
			continue
		}
		segments++
		status := dataString(meta, "status")
		if status == "completed" || status == "aborted" {
			end, ended := ParseStamp(meta["ended_at"])
			if ended {
				boundary(end)
				if end.After(now) {
					terminal, known = false, false
					continue
				}
				if end.After(latestEnd) {
					latestEnd = end
				}
			}
			if interval, ok := requestCompletedInterval(meta); ok {
				intervals = append(intervals, interval)
			} else {
				known = false
			}
			continuation := !ValueBool(meta["is_subagent"]) && dataString(meta, "id") != dataString(group, "id") && (dataString(meta, "continuation_of") != "" || dataString(meta, "root_turn_id") != "")
			if continuation && (finalContinuation == nil || dataString(meta, "ended_at") > dataString(finalContinuation, "ended_at")) {
				finalContinuation = meta
			}
			continue
		}
		terminal = false
		start, started := ParseStamp(meta["started_at"])
		observed, seen := ParseStamp(meta["observed_at"])
		if status != "running" || !seen {
			known = false
			continue
		}
		boundary(observed)
		boundary(observed.Add(requestRunningFreshness))
		if observed.After(now) || !now.Before(observed.Add(requestRunningFreshness)) {
			known = false
			continue
		}
		if started {
			boundary(start)
		}
		if started && start.After(now) {
			known = false
			continue
		}
		running = true
		if !started || meta["started_inferred"] != false {
			known = false
			continue
		}
		if activeStart.IsZero() || start.Before(activeStart) {
			activeStart = start
		}
	}
	group["duration_segments"] = segments
	status := "unknown"
	if running {
		status = "running"
		group["ended_at"] = nil
		if known && !activeStart.IsZero() {
			base := mergedRequestMilliseconds(intervals, &activeStart)
			group["duration_running"], group["duration_started_at"] = true, UTCStamp(activeStart)
			group["duration_base_ms"], group["duration_completed_ms"] = base, base
			group["duration_active_starts"] = []string{UTCStamp(activeStart)}
		}
	} else if terminal && segments > 0 {
		status = firstString(group["status"], "unknown")
		if finalContinuation != nil {
			status = dataString(finalContinuation, "status")
		}
		if !latestEnd.IsZero() {
			group["ended_at"] = UTCStamp(latestEnd)
			if status == "completed" {
				group["completed_at"] = float64(latestEnd.Unix()) + float64(latestEnd.Nanosecond())/1e9
			}
		}
		if known {
			duration := mergedRequestMilliseconds(intervals, nil)
			group["duration_ms"], group["duration_completed_ms"] = duration, duration
		}
	}
	group["status"], group["request_status"] = status, status
	group["status_label"] = map[string]string{"running": "回复中", "completed": "完成", "aborted": "已中断", "unknown": "未知"}[status]
}
