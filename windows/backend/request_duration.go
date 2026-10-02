package backend

import (
	"math"
	"sort"
	"time"
)

const requestDurationProjectionVersion = 2
const requestRunningFreshness = 15 * time.Minute

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
