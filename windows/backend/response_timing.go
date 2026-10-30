package backend

import (
	"context"
	"database/sql"
	"math"
	"strings"
	"time"
)

// One clock per stream, keyed by response ID. Neither the containing turn nor
// a completed output item's timestamp establishes call duration or TTFT.
type upstreamCallTiming struct{ starts map[string]time.Time }

func (c *upstreamCallTiming) observe(value, response Row, event string, at time.Time) Row {
	id := dataString(response, "id")
	if event == "response.created" {
		if c.starts == nil {
			c.starts = map[string]time.Time{}
		}
		if _, exists := c.starts[id]; !exists {
			if len(c.starts) >= 64 {
				oldest := ""
				for key, start := range c.starts {
					if oldest == "" || start.Before(c.starts[oldest]) {
						oldest = key
					}
				}
				delete(c.starts, oldest)
			}
			c.starts[id] = at
		}
		return nil
	}
	if !systemContains([]string{"response.completed", "response.failed", "response.incomplete"}, event) {
		return nil
	}
	start, started := c.starts[id]
	delete(c.starts, id)
	status := map[string]string{"response.completed": "completed", "response.failed": "failed", "response.incomplete": "aborted"}[event]
	fields := Row{"status": status, "timing_version": 1}
	for _, source := range []Row{response, value, ValueRow(response["timing"]), ValueRow(value["timing"])} {
		if ms, ok := validResponseMilliseconds(source["duration_ms"]); ok {
			fields["duration_ms"], fields["duration_source"] = ms, "response"
			break
		}
	}
	if fields["duration_ms"] == nil && started && !at.Before(start) {
		fields["duration_ms"], fields["duration_source"] = at.Sub(start).Seconds()*1000, "response_stream"
		fields["call_started_at"], fields["call_ended_at"] = UTCStamp(start), UTCStamp(at)
	}
	if fields["duration_ms"] == nil {
		for _, source := range []Row{ValueRow(response["timing_metrics"]), ValueRow(value["timing_metrics"])} {
			if ms, ok := validResponseMilliseconds(source["engine_service_total_ms"]); ok {
				fields["duration_ms"], fields["duration_source"] = ms, "response_engine"
				break
			}
		}
	}
	for _, source := range []Row{response, value} {
		if ttft, ok := recordedTTFTMilliseconds(source); ok {
			fields["ttft_ms"], fields["ttft_source"] = ttft, "response"
			break
		}
	}
	return fields
}

func validResponseMilliseconds(value any) (float64, bool) {
	if _, boolean := value.(bool); boolean {
		return 0, false
	}
	ms, ok := ValueFloat(value)
	return ms, ok && ms >= 0 && !math.IsNaN(ms) && !math.IsInf(ms, 0)
}

// Replays fill missing evidence, never replace a saved duration with replay time.
func mergeResponseTiming(old, incoming Row) Row {
	result := CloneRow(old)
	for key, value := range incoming {
		if result[key] == nil {
			result[key] = value
		}
	}
	return result
}

func enrichResponseTimings(ctx context.Context, db *sql.DB, records []Row) error {
	var exists int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='call_timings'").Scan(&exists); err != nil || exists == 0 {
		return err
	}
	args := []any{}
	for _, row := range records {
		if normalizedClient(dataString(row, "client_kind")) == "codex" && dataString(row, "response_id") != "" {
			args = append(args, row["response_id"])
		}
	}
	if len(args) == 0 {
		return nil
	}
	rows, err := db.QueryContext(ctx, "SELECT response_id,data FROM call_timings WHERE response_id IN ("+strings.TrimSuffix(strings.Repeat("?,", len(args)), ",")+")", args...)
	if err != nil {
		return err
	}
	timings := map[string]Row{}
	for rows.Next() {
		var id, raw string
		if err = rows.Scan(&id, &raw); err != nil {
			rows.Close()
			return err
		}
		timings[id], _ = DecodeRow([]byte(raw))
	}
	err = rows.Err()
	rows.Close()
	for _, row := range records {
		if normalizedClient(dataString(row, "client_kind")) != "codex" {
			continue
		}
		for key, value := range timings[dataString(row, "response_id")] {
			if row[key] == nil {
				row[key] = value
			}
		}
	}
	return err
}
