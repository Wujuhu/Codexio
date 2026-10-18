package backend

// Reuse observations.go's response-ID batches before materializing prices.
// The relay only queues changed IDs; no UI read scans or reprices history.
import (
	"context"
	"database/sql"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func (s *Store) markObservedPrice(id string) {
	if id == "" {
		return
	}
	s.mu.Lock()
	s.pendingObservedCount++
	if s.pendingObservedPrices == nil {
		s.pendingObservedPrices = map[string]bool{}
	}
	if len(s.pendingObservedPrices) < 4096 {
		s.pendingObservedPrices[id] = true
	} else {
		s.pendingObservedFull = true
	}
	s.mu.Unlock()
	s.Refresh()
}

type observedPriceSource struct {
	db       *sql.DB
	key      string
	revision int64
	changed  bool
}

func (s *Store) observedPricing(ctx context.Context, tx *sql.Tx, state Row) (observedPriceSource, error) {
	path := filepath.Join(s.options.Directory, "upstream.sqlite")
	stamp := fileStamp(path) + fileStamp(path+"-wal")
	s.mu.Lock()
	pending, full, count := s.pendingObservedPrices, s.pendingObservedFull, s.pendingObservedCount
	s.pendingObservedPrices = map[string]bool{}
	s.pendingObservedFull = false
	s.pendingObservedCount = 0
	s.mu.Unlock()
	source := observedPriceSource{key: s.pricingObservedKey, revision: s.pricingObservedRevision}
	if stamp != s.pricingObservedStamp || count > 0 || full {
		identity := "missing"
		handle, e := os.Open(path)
		if e == nil {
			identity = ledgerFileIdentity(handle)
			handle.Close()
			source.db, e = sql.Open("sqlite", "file:"+filepath.ToSlash(path)+"?mode=ro&_pragma=busy_timeout(100)")
			if e != nil {
				return source, e
			}
			source.db.SetMaxOpenConns(1)
			if e = source.db.QueryRowContext(ctx, "SELECT value FROM revision WHERE id=1").Scan(&source.revision); e != nil {
				source.db.Close()
				return source, e
			}
		} else if !os.IsNotExist(e) {
			return source, e
		} else {
			source.revision = 0
		}
		source.key = identity + "#" + strconv.FormatInt(source.revision, 10)
		s.pricingObservedStamp = stamp
		s.pricingObservedKey = source.key
		s.pricingObservedRevision = source.revision
	}
	source.changed = source.key != dataString(state, "pricing_upstream")
	if !source.changed {
		if source.db != nil {
			source.db.Close()
			source.db = nil
		}
		return source, nil
	}
	priorKey := dataString(state, "pricing_upstream")
	separator := strings.LastIndex(source.key, "#")
	sameIdentity := separator >= 0 && strings.HasPrefix(priorKey, source.key[:separator+1])
	gap := source.revision - ValueInt(state["pricing_upstream_revision"])
	if len(pending) == 0 || !sameIdentity || gap < 0 || gap > count {
		full = true
	}
	if full {
		if _, e := tx.ExecContext(ctx, "INSERT INTO usage_query_changes(kind,item_id) SELECT 'record',id FROM usage_records WHERE coalesce(json_extract(data,'$.client_kind'),'codex')='codex'"); e != nil {
			if source.db != nil {
				source.db.Close()
			}
			return source, e
		}
	} else {
		for id := range pending {
			if _, e := tx.ExecContext(ctx, "INSERT INTO usage_query_changes(kind,item_id) SELECT 'record',id FROM usage_records WHERE json_extract(data,'$.response_id')=?", id); e != nil {
				if source.db != nil {
					source.db.Close()
				}
				return source, e
			}
		}
	}
	return source, nil
}
func observedPriceRows(ctx context.Context, db *sql.DB, records []Row) error {
	if db == nil || len(records) == 0 {
		return nil
	}
	args := []any{}
	for _, r := range records {
		if normalizedClient(dataString(r, "client_kind")) == "codex" {
			if id := dataString(r, "response_id"); id != "" {
				args = append(args, id)
			}
		}
	}
	if len(args) == 0 {
		return nil
	}
	rows, e := db.QueryContext(ctx, "SELECT response_id,model FROM observations WHERE response_id IN ("+strings.TrimSuffix(strings.Repeat("?,", len(args)), ",")+")", args...)
	if e != nil {
		return e
	}
	models := map[string]string{}
	for rows.Next() {
		var id, model string
		if e = rows.Scan(&id, &model); e != nil {
			rows.Close()
			return e
		}
		models[id] = model
	}
	e = rows.Err()
	rows.Close()
	if e != nil {
		return e
	}
	for _, r := range records {
		if normalizedClient(dataString(r, "client_kind")) == "codex" {
			if model := models[dataString(r, "response_id")]; model != "" {
				r["upstream_model"] = model
			}
		}
	}
	return nil
}
