package backend

import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"strings"
)

// Historic response observations remain visible when active collection is off.
func (s *Store) enrichUpstream(rows []Row, grouped bool, contexts ...context.Context) {
	reader := s.readDB(contexts)
	if len(rows) == 0 || s.options.Mock {
		return
	}
	path := filepath.Join(s.options.Directory, "upstream.sqlite")
	if _, e := os.Stat(path); e != nil {
		return
	}
	db, e := sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?mode=ro&_pragma=busy_timeout(100)")
	if e != nil {
		return
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	for _, row := range rows {
		if normalizedClient(dataString(row, "client_kind")) != "codex" {
			continue
		}
		counts := map[string]int{}
		detected, mismatches := 0, 0
		consume := func(batch []Row) {
			if len(batch) == 0 {
				return
			}
			args := make([]any, 0, len(batch))
			for _, r := range batch {
				args = append(args, r["response_id"])
			}
			observed, e := db.QueryContext(reader.ctx, "SELECT response_id,model FROM observations WHERE response_id IN ("+strings.TrimSuffix(strings.Repeat("?,", len(args)), ",")+")", args...)
			if e != nil {
				return
			}
			models := map[string]string{}
			for observed.Next() {
				var id, model string
				if observed.Scan(&id, &model) == nil {
					models[id] = model
				}
			}
			observed.Close()
			for _, r := range batch {
				if upstream := models[dataString(r, "response_id")]; upstream != "" {
					counts[upstream]++
					detected++
					if requested := dataString(r, "model"); requested != "" && requested != upstream {
						mismatches++
					}
				}
			}
		}
		if !grouped {
			consume([]Row{row})
		} else {
			members, e := reader.Query("SELECT json_extract(p.data,'$.response_id'),p.model FROM usage_priced_calls p JOIN usage_request_members m ON p.id=m.record_id WHERE m.request_id=?", row["id"])
			if e != nil {
				continue
			}
			batch := []Row{}
			for members.Next() {
				var response sql.NullString
				var model string
				if members.Scan(&response, &model) != nil {
					break
				}
				if response.Valid && response.String != "" {
					batch = append(batch, Row{"response_id": response.String, "model": model})
				}
				if len(batch) == 500 {
					consume(batch)
					batch = batch[:0]
				}
			}
			members.Close()
			consume(batch)
		}
		if detected > 0 {
			names := map[string]bool{}
			for name := range counts {
				names[name] = true
			}
			models := sortedKeys(names)
			row["upstream_models"] = models
			row["upstream_model_counts"] = counts
			row["upstream_detected_calls"] = detected
			row["upstream_mismatched_calls"] = mismatches
			row["upstream_total_calls"] = 1
			if grouped {
				row["upstream_total_calls"] = row["call_count"]
			}
			row["upstream_model"] = strings.Join(models, " + ")
			row["upstream_mismatched"] = mismatches > 0
		}
	}
}
