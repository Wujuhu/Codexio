package backend

import (
	"context"
	"fmt"
	"io"
	"math"
	"net/http"
	"path/filepath"
	"strings"
	"time"
)

const primaryPrices = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
const referencePrices = "https://models.dev/api.json"

func fetchPriceFeed(ctx context.Context, url string) (Row, error) {
	req, e := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if e != nil {
		return nil, e
	}
	req.Header.Set("User-Agent", "Codexio/0.3.4")
	req.Header.Set("Accept", "application/json")
	client := http.Client{Timeout: 15 * time.Second}
	response, e := client.Do(req)
	if e != nil {
		return nil, e
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("price feed HTTP %d", response.StatusCode)
	}
	b, e := io.ReadAll(io.LimitReader(response.Body, 25000001))
	if e != nil {
		return nil, e
	}
	if len(b) > 25000000 {
		return nil, fmt.Errorf("price feed exceeds limit")
	}
	return DecodeRow(b)
}
func parsePriceFeed(feed Row, primary bool, now string) []Row {
	rows := []Row{}
	if !primary {
		feed = ValueRow(ValueRow(feed["openai"])["models"])
	}
	for model, item := range feed {
		r := ValueRow(item)
		if primary && (dataString(r, "litellm_provider") != "openai" || !(dataString(r, "mode") == "chat" || dataString(r, "mode") == "responses" || dataString(r, "mode") == "completion")) {
			continue
		}
		model = strings.TrimPrefix(model, "openai/")
		if strings.Contains(model, "/") || strings.HasPrefix(model, "ft:") {
			continue
		}
		row := Row{"model": model, "service_tier": "default", "threshold": 0, "updated_at": now, "locked": false, "source": referencePrices}
		if primary {
			row["source"] = primaryPrices
			mapping := map[string]string{"input": "input_cost_per_token", "cache_read": "cache_read_input_token_cost", "cache_write": "cache_creation_input_token_cost", "output": "output_cost_per_token"}
			for k, key := range mapping {
				row[k] = nil
				if n, ok := ValueFloat(r[key]); ok && n >= 0 {
					row[k] = n * 1e6
				}
			}
		} else {
			cost := ValueRow(r["cost"])
			for _, k := range rateFields {
				row[k] = cost[k]
			}
		}
		if validBase(row) {
			rows = append(rows, row)
		}
	}
	return rows
}
func (s *Store) syncPrices(ctx context.Context) {
	s.mu.RLock()
	enabled := s.config["auto_sync_prices"] != false
	s.mu.RUnlock()
	if !enabled {
		return
	}
	path := filepath.Join(s.priceDirectory(), "pricing_cache.json")
	cache, _ := ReadJSON(path)
	if checked, ok := ParseStamp(cache["checked_at"]); ok {
		retry := 24 * time.Hour
		if dataString(cache, "status") == "offline" {
			retry = time.Hour
		}
		elapsed := time.Since(checked)
		if elapsed >= 0 && elapsed < retry {
			return
		}
	}
	type answer struct {
		rows []Row
		err  error
	}
	primaryCh, referenceCh := make(chan answer, 1), make(chan answer, 1)
	now := UTCStamp(time.Now())
	go func() {
		r, e := fetchPriceFeed(ctx, primaryPrices)
		primaryCh <- answer{parsePriceFeed(r, true, now), e}
	}()
	go func() {
		r, e := fetchPriceFeed(ctx, referencePrices)
		referenceCh <- answer{parsePriceFeed(r, false, now), e}
	}()
	primary, reference := <-primaryCh, <-referenceCh
	s.mu.RLock()
	enabled = s.config["auto_sync_prices"] != false
	s.mu.RUnlock()
	if !enabled || ctx.Err() != nil {
		return
	}
	rows := primary.rows
	status := "synced"
	if len(rows) == 0 {
		rows = reference.rows
		status = "fallback"
	}
	if len(rows) == 0 {
		cache["status"] = "offline"
		cache["checked_at"] = now
		cache["error"] = fmt.Sprint(primary.err, "; ", reference.err)
		_ = WriteJSON(path, cache)
		s.mu.Lock()
		s.priceStatus = Row{"status": "offline", "error": cache["error"], "updated_at": cache["updated_at"]}
		s.mu.Unlock()
		return
	}
	refs := map[string]Row{}
	for _, r := range reference.rows {
		refs[dataString(r, "model")] = r
	}
	conflicts := map[string]bool{}
	if len(primary.rows) > 0 && len(reference.rows) > 0 {
		for _, r := range primary.rows {
			model := dataString(r, "model")
			other := refs[model]
			if other == nil {
				continue
			}
			for _, k := range rateFields {
				a, ok := ValueFloat(r[k])
				b, ok2 := ValueFloat(other[k])
				if ok && ok2 && math.Abs(a-b) > math.Max(1e-9, math.Max(math.Abs(a), math.Abs(b))*1e-6) {
					conflicts[model] = true
				}
			}
		}
	}
	retained := map[string]Row{}
	for _, r := range s.Prices() {
		if ValueInt(r["threshold"]) == 0 && dataString(r, "service_tier") == "default" {
			retained[dataString(r, "model")] = r
		}
	}
	accepted := []Row{}
	for _, r := range rows {
		model := dataString(r, "model")
		if conflicts[model] {
			if prior := retained[model]; prior != nil && !ValueBool(prior["locked"]) {
				accepted = append(accepted, prior)
			}
			continue
		}
		accepted = append(accepted, r)
	}
	if len(conflicts) > 0 {
		status = "conflict"
	}
	cache = Row{"rows": accepted, "status": status, "checked_at": now, "updated_at": now, "conflicting_models": sortedKeys(conflicts), "verification_status": "checked"}
	if len(reference.rows) == 0 {
		cache["verification_status"] = "not_available"
	}
	if len(conflicts) > 0 {
		cache["error"] = "两源单价冲突，保留已有价格"
	}
	if WriteJSON(path, cache) == nil {
		_ = s.loadPrices()
	}
}
