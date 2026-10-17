package backend

import (
	"context"
	"database/sql"
	"time"
)

// Same fifteen-minute activity window as ClaudeLedger.observeSurfaceRequest on
// Mac. Freshness is separate from metering: repeated progress cannot reprice or
// recount a request, and only a visible state transition invalidates its group.
func claudeActivityStatus(entry Row) string {
	switch dataString(entry, "type") {
	case "assistant":
		message := ValueRow(entry["message"])
		if ValueBool(entry["isApiErrorMessage"]) || dataString(message, "model") == "<synthetic>" {
			return "failed"
		}
		if stop := dataString(message, "stop_reason"); stop == "end_turn" || stop == "stop_sequence" {
			return "completed"
		}
		return "running"
	case "user":
		if !ValueBool(entry["isMeta"]) {
			return "running"
		}
	case "progress":
		return "running"
	case "system":
		if dataString(entry, "subtype") == "turn_duration" {
			return "completed"
		}
	}
	return ""
}
func observeClaudeActivity(tx *sql.Tx, request, stamp, status string, now time.Time) error {
	at, ok := ParseStamp(stamp)
	if !ok || request == "" || status == "" || at.After(now) || !at.After(now.Add(-15*time.Minute)) {
		return nil
	}
	var previous, previousStatus string
	_ = tx.QueryRow("SELECT observed_at,status FROM claude_live_requests WHERE id=?", request).Scan(&previous, &previousStatus)
	if previous > stamp || previous == stamp && (previousStatus == status || previousStatus != "running") {
		return nil
	}
	if _, err := tx.Exec(`INSERT INTO claude_live_requests VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET observed_at=excluded.observed_at,status=excluded.status`, request, stamp, status); err != nil {
		return err
	}
	prior, known := ParseStamp(previous)
	if previousStatus != status || !known || !prior.After(now.Add(-15*time.Minute)) || prior.After(now) {
		if _, err := tx.Exec("INSERT INTO usage_query_changes(kind,item_id) VALUES('claude_request',?)", request); err != nil {
			return err
		}
		if _, err := tx.Exec("UPDATE usage_revisions SET revision=revision+1 WHERE kind='ledger'"); err != nil {
			return err
		}
	}
	_, err := tx.Exec("DELETE FROM claude_live_requests WHERE id IN(SELECT id FROM claude_live_requests ORDER BY observed_at DESC,id DESC LIMIT -1 OFFSET 256)")
	return err
}
func (s *Store) prepareClaudeActivity(ctx context.Context, ns string) error {
	if s.claudeLivePrepared {
		return nil
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	now := time.Now()
	requests, err := queryJSON(tx, `SELECT data FROM claude_requests WHERE id LIKE ? AND json_extract(data,'$.timestamp')>? AND json_extract(data,'$.timestamp')<=? AND coalesce(json_extract(data,'$.request_status'),'unknown') NOT IN('completed','failed','aborted') AND NOT EXISTS(SELECT 1 FROM claude_live_requests l WHERE l.id=claude_requests.id) ORDER BY json_extract(data,'$.timestamp') DESC LIMIT 64`, ns+"%", ledgerStamp(now.Add(-15*time.Minute)), ledgerStamp(now))
	if err != nil {
		return err
	}
	for _, request := range requests {
		var raw string
		_ = tx.QueryRow(`SELECT data FROM usage_records WHERE json_extract(data,'$.client_kind')='claude_code' AND json_extract(data,'$.claude_request_id')=? AND coalesce(json_extract(data,'$.is_subagent'),0)=0 ORDER BY timestamp DESC,id DESC LIMIT 1`, request["id"]).Scan(&raw)
		call := dataRow(raw)
		timing := ValueRow(call["call_timing"])
		if len(call) > 0 && dataString(timing, "stop") != "tool_use" {
			continue
		}
		stamp := firstString(timing["last"], request["timestamp"])
		if err = observeClaudeActivity(tx, dataString(request, "id"), stamp, "running", now); err != nil {
			return err
		}
	}
	if err = tx.Commit(); err == nil {
		s.claudeLivePrepared = true
	}
	return err
}
func applyClaudeActivity(tx *sql.Tx, group Row, now time.Time) error {
	group["duration_refresh_at"] = nil
	group["duration_valid_after"] = nil
	var stamp, status string
	if err := tx.QueryRow("SELECT observed_at,status FROM claude_live_requests WHERE id=?", group["id"]).Scan(&stamp, &status); err != nil {
		if err == sql.ErrNoRows {
			return nil
		}
		return err
	}
	observed, ok := ParseStamp(stamp)
	if !ok {
		return nil
	}
	if observed.After(now) {
		group["duration_refresh_at"] = mobileUnixSeconds(observed)
		return nil
	}
	ended, hasEnd := ParseStamp(dataString(group, "ended_at"))
	if hasEnd && !ended.Before(observed) {
		return nil
	}
	if status == "running" {
		expires := observed.Add(15 * time.Minute)
		if !expires.After(now) {
			return nil
		}
		group["duration_refresh_at"] = mobileUnixSeconds(expires)
		group["duration_valid_after"] = mobileUnixSeconds(observed)
	} else {
		group["ended_at"] = stamp
	}
	group["status"] = status
	group["request_status"] = status
	group["duration_running"] = false
	group["duration_ms"] = nil
	group["duration_source"] = nil
	if status == "running" {
		group["ended_at"] = nil
	}
	return nil
}
