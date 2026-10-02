package backend

import (
	"encoding/json"
	"time"
)

const mobileTextRetention = int64(7 * 86400)

// Request timestamps use fractional Unix seconds, matching MobileProtocol.swift.
// ValueInt rejects non-integral numbers, so reuse the ledger timestamp parser.
func mobileContentSeconds(value any) int64 {
	stamp, ok := ParseStamp(value)
	if !ok {
		return 0
	}
	return stamp.Unix()
}

func mobileDetailExpires(value Row) int64 {
	return max(mobileContentSeconds(value["started"]), mobileContentSeconds(value["completed"])) + mobileTextRetention
}

// Match the Worker retention projection. Removing an expired preview never
// touches the local raw ledger or its aggregate metric datasets.
func mobileRetainValue(kind string, value any, now int64) (any, bool, int64) {
	changed := false
	next := int64(0)
	deadline := func(at int64) {
		if at > now && (next == 0 || at < next) {
			next = at
		}
	}
	validRequest := func(r Row) bool {
		started := mobileContentSeconds(r["started"])
		return started > now-mobileTextRetention && started <= now+300
	}
	switch kind {
	case "recent":
		rows := ValueRows(value)
		kept := make([]Row, 0, len(rows))
		for _, row := range rows {
			if validRequest(row) {
				kept = append(kept, row)
				deadline(mobileContentSeconds(row["started"]) + mobileTextRetention)
			} else {
				changed = true
			}
		}
		value = kept
	case "live":
		r := ValueRow(value)
		if task := ValueRow(r["task"]); len(task) > 0 {
			if !validRequest(task) {
				r["task"] = nil
				changed = true
			} else {
				deadline(mobileContentSeconds(task["started"]) + mobileTextRetention)
			}
		}
	case "detail":
		r := ValueRow(value)
		deadline(mobileDetailExpires(r))
		for _, attachment := range ValueRows(r["attachments"]) {
			if attachment["thumbnail"] == nil {
				continue
			}
			at := mobileContentSeconds(r["started"]) + mobileImageRetention
			if at <= now {
				attachment["thumbnail"] = nil
				changed = true
			} else {
				deadline(at)
			}
		}
		for _, image := range ValueRows(r["images"]) {
			if ValueString(image["availability"]) != "expired" {
				if at := ValueInt(image["expires"]); at <= now {
					image["availability"] = "expired"
					changed = true
				} else {
					deadline(at)
				}
			}
		}
	}
	return value, changed, next
}

func (m *MobileHost) noteContentExpiryLocked(at int64) {
	if at > 0 && (m.nextContentExpiry == 0 || at < m.nextContentExpiry) {
		m.nextContentExpiry = at
	}
}

// Called before serving and on the existing sync cadence. Unchanged content is
// not decoded again before its next original-content deadline.
func (m *MobileHost) retainContentLocked() bool {
	now := time.Now().Unix()
	if m.nextContentExpiry == 0 || now < m.nextContentExpiry {
		return false
	}
	m.nextContentExpiry = 0
	changed := false
	retain := func(kind string, envelope Row) Row {
		var value any
		if json.Unmarshal([]byte(ValueString(envelope["payload"])), &value) != nil {
			return envelope
		}
		retained, altered, next := mobileRetainValue(kind, value, now)
		m.noteContentExpiryLocked(next)
		if !altered {
			return envelope
		}
		raw, err := mobileJSON(retained)
		if err != nil {
			return envelope
		}
		changed = true
		return Row{"dataset": envelope["dataset"], "revision": max(ValueInt(envelope["revision"]), time.Now().UnixMilli()) + 1, "digest": HashString(string(raw)), "payload": string(raw)}
	}
	for _, name := range []string{"live", "recent"} {
		if envelope := m.datasets[name]; envelope != nil {
			m.datasets[name] = retain(name, envelope)
		}
	}
	if m.cloudRecent != nil {
		old := ValueString(m.cloudRecent["digest"])
		m.cloudRecent = retain("recent", m.cloudRecent)
		if old != ValueString(m.cloudRecent["digest"]) {
			m.cloudRecentNeedsUpload = true
			m.sent["recent"] = 0
		}
	}
	for id, detail := range m.details {
		if detail.Expires <= float64(now) {
			delete(m.details, id)
			delete(m.sentDetails, id)
			changed = true
			continue
		}
		m.noteContentExpiryLocked(int64(detail.Expires))
		detail.Envelope = retain("detail", detail.Envelope)
	}
	if changed {
		if err := m.saveProjectionLocked(); err != nil {
			m.lastError = err.Error()
			m.lastErrorTemporary = false
		}
	}
	return changed
}
