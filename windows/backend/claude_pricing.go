package backend

import (
	"context"
	_ "embed"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

//go:embed assets/claude_pricing_seed.json
var claudePriceSeed []byte
var claudePriceFields = []string{"input", "cache_read", "cache_write", "cache_write_1h", "output"}

const claudePriceSource = "https://platform.claude.com/docs/en/about-claude/pricing"

func claudePriceID(v string) string {
	return strings.ReplaceAll(strings.ToLower(strings.TrimSpace(v)), "[1m]", "")
}
func claudePriceLabel(v string) string {
	return strings.Map(func(r rune) rune {
		if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, strings.ToLower(v))
}
func (s *Store) claudePriceDirectory() string { return filepath.Join(s.priceDirectory(), "claude") }
func claudePriceIdentifiers(rows []Row) (map[string]string, map[string]string) {
	ids, labels := map[string]string{}, map[string]string{}
	for _, row := range rows {
		model := dataString(row, "model")
		ids[claudePriceID(model)] = model
		for _, alias := range ValueStrings(row["aliases"]) {
			ids[claudePriceID(alias)] = model
		}
		label := dataString(row, "display_name")
		labels[claudePriceLabel(label)] = model
		labels[claudePriceLabel(strings.TrimPrefix(label, "Claude "))] = model
	}
	return ids, labels
}
func (s *Store) loadClaudePrices() error {
	seed, _ := DecodeRow(claudePriceSeed)
	cache, _ := ReadJSON(filepath.Join(s.claudePriceDirectory(), "cache.json"))
	overrides, _ := ReadJSON(filepath.Join(s.claudePriceDirectory(), "overrides.json"))
	aliases, _ := ReadJSON(filepath.Join(s.claudePriceDirectory(), "aliases.json"))
	if aliases == nil {
		aliases = Row{}
	}
	rows := []Row{}
	content := []Row{}
	for _, original := range ValueRows(seed["rows"]) {
		row := CloneRow(original)
		for _, cached := range ValueRows(cache["rows"]) {
			if dataString(cached, "model") == dataString(row, "model") && ValueInt(cached["threshold"]) == ValueInt(row["threshold"]) {
				for _, field := range claudePriceFields {
					if cached[field] != nil {
						row[field] = cached[field]
					}
				}
				break
			}
		}
		if manual := ValueRow(overrides[dataString(row, "model")]); len(manual) > 0 && ValueInt(row["threshold"]) == 0 {
			for _, key := range claudePriceFields {
				row[key] = manual[key]
			}
			row["locked"] = true
		}
		rows = append(rows, row)
		item := Row{}
		for _, key := range append([]string{"model", "aliases", "threshold", "fast_multiplier"}, claudePriceFields...) {
			item[key] = row[key]
		}
		content = append(content, item)
	}
	version := HashString(dataJSON(Row{"rule": seed["rule_version"], "rows": content, "aliases": aliases}))
	s.mu.Lock()
	s.claudePrices = rows
	s.claudePriceVersion = version
	s.claudeAliases = aliases
	s.claudePriceStatus = Row{"updated_at": cache["updated_at"], "status": "bundled", "error": ""}
	if cache["updated_at"] != nil {
		s.claudePriceStatus["status"] = "synced"
	}
	if ValueBool(cache["failed"]) {
		s.claudePriceStatus["status"] = "offline"
		s.claudePriceStatus["error"] = "价格同步失败，保留已有价格"
	}
	s.priceVersion = HashString(s.codexPriceVersion + "|" + version)[:16]
	s.mu.Unlock()
	archive := filepath.Join(s.claudePriceDirectory(), "versions", version+".json")
	if _, e := os.Stat(archive); os.IsNotExist(e) {
		return WriteJSON(archive, Row{"rows": rows, "aliases": aliases, "rule_version": seed["rule_version"]})
	}
	return nil
}
func (s *Store) observeClaudePriceLabels(root string) error {
	path := filepath.Join(root, "settings.json")
	info, e := os.Stat(path)
	if e != nil {
		return nil
	}
	stamp := fmt.Sprintf("%d:%d", info.ModTime().UnixNano(), info.Size())
	if stamp == s.claudePricingStamp {
		return nil
	}
	s.claudePricingStamp = stamp
	settings, e := systemReadJSON(path, 1<<20)
	if e != nil {
		return nil
	}
	s.mu.RLock()
	_, labels := claudePriceIdentifiers(s.claudePrices)
	aliases := CloneRow(s.claudeAliases)
	s.mu.RUnlock()
	changed := false
	scope := HashString(filepath.Clean(root)) + ":"
	current := map[string]string{}
	for _, option := range ValueRows(ValueRow(settings["modelPicker"])["options"]) {
		raw := dataString(option, "model")
		if raw != "" && len(raw) <= 256 {
			current[scope+raw] = labels[claudePriceLabel(dataString(option, "label"))]
		}
	}
	for key := range aliases {
		if strings.HasPrefix(key, scope) {
			if _, ok := current[key]; !ok {
				current[key] = ""
			}
		}
	}
	now := float64(time.Now().UnixMilli()) / 1000
	for key, model := range current {
		saved := ValueRow(aliases[key])
		history := ValueRows(saved["history"])
		if len(history) == 0 && saved["since"] != nil {
			history = []Row{saved}
		}
		previous := ""
		since := 0.0
		if len(history) > 0 {
			previous = dataString(history[len(history)-1], "model")
			since, _ = ValueFloat(history[len(history)-1]["since"])
		}
		if model == previous || now <= since || aliases[key] == nil && len(aliases) >= 2000 {
			continue
		}
		if len(history) >= 128 {
			model = ""
		}
		if model == previous {
			continue
		}
		next := append(append([]Row{}, history...), Row{"model": model, "since": now})
		aliases[key] = Row{"history": next}
		changed = true
	}
	if changed {
		if e = WriteJSON(filepath.Join(s.claudePriceDirectory(), "aliases.json"), aliases); e != nil {
			return e
		}
		return s.loadClaudePrices()
	}
	return nil
}
func (s *Store) priceClaude(r Row) Row {
	s.mu.RLock()
	rows := s.claudePrices
	aliases := s.claudeAliases
	version := s.claudePriceVersion
	s.mu.RUnlock()
	r["cost_usd"] = nil
	r["pricing_status"] = "unpriced"
	r["price_version"] = version
	r["pricing_basis"] = "official_api_reference"
	delete(r, "price_rates")
	delete(r, "pricing_mode")
	ids, _ := claudePriceIdentifiers(rows)
	returned := dataString(r, "upstream_model")
	if returned == "" {
		return r
	}
	model := ids[claudePriceID(returned)]
	estimated := false
	if model == "" {
		// Bind an alias only to the returned ID, never to a different request.
		saved := ValueRow(aliases[HashString(filepath.Clean(claudeRoot()))+":"+returned])
		history := ValueRows(saved["history"])
		if len(history) == 0 {
			history = []Row{saved}
		}
		if stamp, ok := ParseStamp(r["timestamp"]); ok {
			at := float64(stamp.UnixMilli()) / 1000
			for i := len(history) - 1; i >= 0; i-- {
				since, known := ValueFloat(history[i]["since"])
				if known && at >= since {
					model = ids[dataString(history[i], "model")]
					estimated = model != ""
					break
				}
			}
		}
	}
	if model == "" || ValueBool(r["usage_conflict"]) {
		return r
	}
	counts := []int64{}
	for _, key := range []string{"raw_input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens"} {
		n, ok := knownCount(r[key])
		if !ok {
			return r
		}
		counts = append(counts, n)
	}
	if counts[0] > math.MaxInt64-counts[1] || counts[0]+counts[1] > math.MaxInt64-counts[2] {
		return r
	}
	total := counts[0] + counts[1] + counts[2]
	var selected Row
	threshold := int64(-1)
	for _, row := range rows {
		boundary := ValueInt(row["threshold"])
		if dataString(row, "model") == model && (boundary == 0 || total > boundary) && boundary > threshold {
			selected = CloneRow(row)
			threshold = boundary
		}
	}
	if selected == nil {
		return r
	}
	tier := strings.ToLower(dataString(r, "service_tier"))
	fast := tier == "priority" || tier == "fast"
	if tier != "" && tier != "default" && tier != "standard" && !fast {
		return r
	}
	estimated = estimated || tier == ""
	if fast {
		multiple, ok := ValueFloat(selected["fast_multiplier"])
		if !ok || multiple <= 0 {
			return r
		}
		for _, key := range claudePriceFields {
			v, _ := ValueFloat(selected[key])
			selected[key] = v * multiple
		}
		r["pricing_mode"] = "credits"
	}
	five, ok5 := knownCount(r["cache_write_5m_tokens"])
	hour, ok1 := knownCount(r["cache_write_1h_tokens"])
	if counts[2] == 0 {
		five, hour, ok5, ok1 = 0, 0, true, true
	}
	if r["cache_write_5m_tokens"] != nil && !ok5 || r["cache_write_1h_tokens"] != nil && !ok1 {
		return r
	}
	if !ok5 || !ok1 {
		five, hour = counts[2], 0
		estimated = true
	}
	if five > math.MaxInt64-hour || five+hour != counts[2] {
		return r
	}
	tokens := []int64{counts[0], counts[1], five, hour, counts[3]}
	cost := 0.0
	for i, key := range claudePriceFields {
		rate, ok := ValueFloat(selected[key])
		if tokens[i] > 0 && !ok {
			return r
		}
		cost += float64(tokens[i]) * rate / 1e6
	}
	if math.IsNaN(cost) || math.IsInf(cost, 0) {
		return r
	}
	r["cost_usd"] = cost
	r["price_rates"] = selected
	r["pricing_status"] = "priced"
	if estimated {
		r["pricing_status"] = "estimated"
	}
	return r
}
func (s *Store) ClaudePrices() Row {
	s.mu.RLock()
	defer s.mu.RUnlock()
	rows := make([]Row, len(s.claudePrices))
	for i, r := range s.claudePrices {
		rows[i] = CloneRow(r)
		rows[i]["condition"] = "Standard"
		if dataString(r, "model") == "claude-haiku-5-5" {
			if ValueInt(r["threshold"]) > 0 {
				rows[i]["condition"] = "Standard · >100K"
			} else {
				rows[i]["condition"] = "Standard · ≤100K"
			}
		}
	}
	return Row{"rows": rows, "version": s.claudePriceVersion, "status": CloneRow(s.claudePriceStatus), "client_kind": "claude_code", "basis": "Standard API"}
}
func (s *Store) SetClaudePrice(model string, rates Row) error {
	s.work.Lock()
	defer s.work.Unlock()
	path := filepath.Join(s.claudePriceDirectory(), "overrides.json")
	overrides, _ := ReadJSON(path)
	if overrides == nil {
		overrides = Row{}
	}
	if len(rates) == 0 {
		delete(overrides, model)
	} else {
		seed, _ := DecodeRow(claudePriceSeed)
		ids, _ := claudePriceIdentifiers(ValueRows(seed["rows"]))
		if ids[model] != model {
			return errors.New("模型不在官方价格目录中")
		}
		clean := Row{}
		for _, key := range claudePriceFields {
			v, ok := ValueFloat(rates[key])
			if !ok || v < 0 || math.IsNaN(v) || math.IsInf(v, 0) {
				return errors.New("请输入有限的非负单价")
			}
			clean[key] = v
		}
		overrides[model] = clean
	}
	if e := WriteJSON(path, overrides); e != nil {
		return e
	}
	if e := s.loadClaudePrices(); e != nil {
		return e
	}
	s.Refresh()
	return nil
}
func (s *Store) syncClaudePrices(ctx context.Context, force bool) error {
	s.mu.RLock()
	enabled := ValueBool(s.config["auto_sync_prices"])
	s.mu.RUnlock()
	if !force && !enabled {
		return nil
	}
	path := filepath.Join(s.claudePriceDirectory(), "cache.json")
	cache, _ := ReadJSON(path)
	if cache == nil {
		cache = Row{}
	}
	interval := 24 * time.Hour
	if ValueBool(cache["failed"]) {
		interval = time.Hour
	}
	if checked, ok := ParseStamp(cache["checked_at"]); !force && ok && time.Since(checked) < interval {
		return nil
	}
	req, e := http.NewRequestWithContext(ctx, http.MethodGet, claudePriceSource+".md", nil)
	if e != nil {
		return e
	}
	req.Header.Set("User-Agent", "Codexio")
	response, e := (&http.Client{Timeout: 20 * time.Second, Transport: systemAccountTransport}).Do(req)
	var body []byte
	if e == nil {
		defer response.Body.Close()
		if response.StatusCode != 200 {
			e = fmt.Errorf("Claude pricing HTTP %d", response.StatusCode)
		} else {
			body, e = io.ReadAll(io.LimitReader(response.Body, 2000001))
		}
	}
	var fetched []Row
	if e == nil && len(body) <= 2000000 {
		fetched, e = parseClaudePriceTable(string(body))
	}
	if e == nil && len(fetched) == 0 {
		e = errors.New("价格目录无有效内容")
	}
	if e != nil {
		cache["checked_at"] = UTCStamp(time.Now())
		cache["failed"] = true
		_ = WriteJSON(path, cache)
		_ = s.loadClaudePrices()
		return e
	}
	cache = Row{"rows": fetched, "updated_at": UTCStamp(time.Now()), "checked_at": UTCStamp(time.Now()), "failed": false}
	if e = WriteJSON(path, cache); e != nil {
		return e
	}
	return s.loadClaudePrices()
}
func parseClaudePriceTable(text string) ([]Row, error) {
	table := ""
	for _, block := range strings.Split(text, "\n\n") {
		if strings.Contains(block, "| Model") && strings.Contains(block, "5m cache writes") {
			table = block
			break
		}
	}
	seed, _ := DecodeRow(claudePriceSeed)
	rows := ValueRows(seed["rows"])
	price := regexp.MustCompile(`\$([0-9]+(?:\.[0-9]+)?)`)
	for _, row := range rows {
		name := dataString(row, "display_name")
		var chosen string
		for _, line := range strings.Split(table, "\n") {
			prefix := "| " + name
			if !strings.HasPrefix(line, prefix) {
				continue
			}
			tail := strings.TrimPrefix(line, prefix)
			if tail == "" || (tail[0] != ' ' && tail[0] != '|') {
				continue
			}
			if dataString(row, "model") == "claude-haiku-5-5" {
				marker := "up to 100,000"
				if ValueInt(row["threshold"]) > 0 {
					marker = "over 100,000"
				}
				if !strings.Contains(line, marker) {
					continue
				}
			}
			chosen = line
			break
		}
		matches := price.FindAllStringSubmatch(chosen, -1)
		if len(matches) != 5 {
			return nil, errors.New("价格目录缺少已有型号，保留已有价格")
		}
		for i, key := range []string{"input", "cache_write", "cache_write_1h", "cache_read", "output"} {
			v, e := strconv.ParseFloat(matches[i][1], 64)
			if e != nil || math.IsInf(v, 0) || math.IsNaN(v) {
				return nil, errors.New("单价无效")
			}
			row[key] = v
		}
	}
	return rows, nil
}
