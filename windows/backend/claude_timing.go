package backend

import (
	"bufio"
	"context"
	"math"
	"os"
	"strings"
	"time"
)

// Timing follows source events, independently of the selected usage fragment.
func claudeTimingObservation(entry Row) Row {
	stamp, ok := ParseStamp(dataString(entry, "timestamp"))
	if !ok {
		return Row{}
	}
	value := ledgerStamp(stamp)
	r := Row{"first": value, "last": value}
	if stop := dataString(ValueRow(entry["message"]), "stop_reason"); stop != "" {
		r["terminal"] = value
		r["stop"] = stop
	}
	return r
}
func mergeClaudeTiming(old, next Row) Row {
	r := CloneRow(old)
	for _, key := range []string{"first", "last"} {
		date, ok := ParseStamp(dataString(next, key))
		if !ok {
			continue
		}
		prior, known := ParseStamp(dataString(old, key))
		if !known || key == "first" && date.Before(prior) || key == "last" && date.After(prior) {
			r[key] = next[key]
		}
	}
	if date, ok := ParseStamp(dataString(next, "terminal")); ok {
		prior, known := ParseStamp(dataString(old, "terminal"))
		if known && date.Equal(prior) && dataString(old, "stop") != dataString(next, "stop") {
			r["conflict"] = true
		}
		if !known || date.After(prior) {
			r["terminal"] = next["terminal"]
			r["stop"] = next["stop"]
		}
	}
	if ValueBool(next["conflict"]) {
		r["conflict"] = true
	}
	return r
}
func claudeObservedTime(call Row) time.Time {
	t, ok := ParseStamp(dataString(ValueRow(call["call_timing"]), "last"))
	if !ok {
		t, _ = ParseStamp(dataString(call, "timestamp"))
	}
	return t
}
func claudeCallStart(call Row) time.Time {
	t, ok := ParseStamp(dataString(ValueRow(call["call_timing"]), "first"))
	if !ok {
		t, _ = ParseStamp(dataString(call, "timestamp"))
	}
	return t
}
func claudeRequestTiming(request Row, main []Row) Row {
	r := Row{}
	derived := dataString(request, "duration_source") == "main_call"
	explicit, known := ValueFloat(request["explicit_duration_ms"])
	endValue := request["explicit_ended_at"]
	if !known && !derived {
		explicit, known = ValueFloat(request["duration_ms"])
		endValue = request["ended_at"]
	}
	if known {
		r["explicit_duration_ms"] = explicit
		r["explicit_ended_at"] = endValue
	}
	latest := Row{}
	if len(main) > 0 {
		latest = main[0]
	}
	observed := claudeObservedTime(latest)
	status := firstString(request["request_status"], request["status"])
	failed := status == "failed" || status == "aborted"
	if end, ok := ParseStamp(ValueString(endValue)); known && explicit >= 0 && ok && (observed.IsZero() || !end.Before(observed)) {
		r["duration_ms"] = explicit
		r["ended_at"] = endValue
		r["duration_source"] = "turn_duration"
		if !failed {
			r["status"] = "completed"
			r["request_status"] = "completed"
		}
		return r
	}
	r["duration_ms"] = nil
	r["ended_at"] = nil
	r["duration_source"] = nil
	if status == "completed" {
		r["status"] = "unknown"
		r["request_status"] = "unknown"
	}
	timing := ValueRow(latest["call_timing"])
	stop := dataString(timing, "stop")
	start, startOK := ParseStamp(firstString(request["started_at"], request["timestamp"]))
	end, endOK := ParseStamp(dataString(timing, "terminal"))
	if failed || !ValueBool(latest["call_timing_checked"]) || ValueBool(latest["call_timing_incomplete"]) || ValueBool(timing["conflict"]) || (stop != "end_turn" && stop != "stop_sequence") || !startOK || !endOK || observed.IsZero() || end.Before(observed) || end.Before(start) {
		return r
	}
	if len(main) > 1 && !claudeCallStart(main[1]).Before(claudeCallStart(latest)) {
		return r
	}
	ms := math.Round(end.Sub(start).Seconds() * 1000)
	if math.IsNaN(ms) || math.IsInf(ms, 0) {
		return r
	}
	r["duration_ms"] = ms
	r["ended_at"] = ledgerStamp(end)
	r["duration_source"] = "main_call"
	r["status"] = "completed"
	r["request_status"] = "completed"
	return r
}

// Upgrade only indexed metadata, in bounded batches on the existing scan queue.
// Never re-ingest usage or inspect unrelated transcript bodies.
func (s *Store) recoverClaudeCallTiming(ctx context.Context, ns string) error {
	tx, e := s.db.Begin()
	if e != nil {
		return e
	}
	calls, e := queryJSON(tx, `SELECT data FROM usage_records WHERE instr(id,?)=1 AND json_extract(data,'$.client_kind')='claude_code' AND coalesce(json_extract(data,'$.call_timing_checked'),0)=0 AND coalesce(json_extract(data,'$.is_subagent'),0)=0 ORDER BY id LIMIT 32`, ns)
	tx.Rollback()
	if e != nil {
		return e
	}
	remainingBatch := 32_000_000
	for _, call := range calls {
		if remainingBatch <= 0 {
			break
		}
		if e = ctx.Err(); e != nil {
			return e
		}
		id := dataString(call, "id")
		if !ValueBool(call["call_timing_checked"]) {
			rows, err := s.db.Query("SELECT uuid,path,offset,identity,line_hash,available FROM claude_locators WHERE item_id=? ORDER BY path,offset LIMIT 65", id)
			if err != nil {
				return err
			}
			locators := []Row{}
			for rows.Next() {
				var uuid, path, identity, hash string
				var offset int64
				var available int
				if err = rows.Scan(&uuid, &path, &offset, &identity, &hash, &available); err != nil {
					rows.Close()
					return err
				}
				locators = append(locators, Row{"uuid": uuid, "path": path, "offset": offset, "identity": identity, "line_hash": hash, "available": available})
			}
			err = rows.Err()
			rows.Close()
			if err != nil {
				return err
			}
			complete := len(locators) > 0 && len(locators) <= 64
			timing := Row{}
			remaining := 32_000_000
			for i, locator := range locators {
				if e = ctx.Err(); e != nil {
					return e
				}
				if i >= 64 || remaining <= 0 {
					complete = false
					break
				}
				if ValueInt(locator["available"]) != 1 {
					complete = false
					continue
				}
				f, err := os.Open(dataString(locator, "path"))
				if err != nil {
					complete = false
					continue
				}
				if ledgerFileIdentity(f) != dataString(locator, "identity") {
					f.Close()
					complete = false
					continue
				}
				_, err = f.Seek(ValueInt(locator["offset"]), 0)
				if err != nil {
					f.Close()
					complete = false
					continue
				}
				line, err := readBoundedLine(bufio.NewReaderSize(f, 65536), min(8_000_000, remaining))
				f.Close()
				remaining -= len(line)
				remainingBatch -= len(line)
				if err != nil || HashString(string(line)) != dataString(locator, "line_hash") {
					complete = false
					continue
				}
				entry, err := DecodeRow(line)
				if err != nil || dataString(entry, "uuid") != dataString(locator, "uuid") || dataString(entry, "type") != "assistant" || dataString(ValueRow(entry["message"]), "id") != dataString(call, "response_id") || ValueBool(entry["isSidechain"]) || dataString(entry, "agentId") != "" || strings.Contains(strings.ReplaceAll(dataString(locator, "path"), "\\", "/"), "/subagents/") {
					complete = false
					continue
				}
				timing = mergeClaudeTiming(timing, claudeTimingObservation(entry))
			}
			call["call_timing"] = timing
			call["call_timing_checked"] = true
			call["call_timing_incomplete"] = !complete
			// Only metadata changes: the existing record trigger invalidates affected projections.
			if _, e = s.db.Exec("UPDATE usage_records SET data=? WHERE id=?", dataJSON(call), id); e != nil {
				return e
			}
		}
	}
	return nil
}
