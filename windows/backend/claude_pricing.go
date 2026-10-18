package backend

import (
	"context"
	_ "embed"
	"fmt"
	"os"
	"path/filepath"
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
	aliases, _ := ReadJSON(filepath.Join(s.claudePriceDirectory(), "aliases.json"))
	if aliases == nil {
		aliases = Row{}
	}
	rows := []Row{}
	for _, row := range frozenPrices.catalog {
		if dataString(row, "group") == "claude" {
			rows = append(rows, row)
		}
	}
	version := HashString(frozenPrices.version + "|" + dataJSON(aliases))
	s.mu.Lock()
	s.claudePrices = rows
	s.claudePriceVersion = version
	s.claudeAliases = aliases
	s.claudePriceStatus = Row{"status": "bundled", "updated_at": frozenPrices.rules["verified_at"]}
	s.priceVersion = HashString(s.codexPriceVersion + "|" + version)
	s.mu.Unlock()
	archive := filepath.Join(s.claudePriceDirectory(), "versions", version+".json")
	if _, e := os.Stat(archive); os.IsNotExist(e) {
		return WriteJSON(archive, Row{"catalog": frozenPrices.version, "rows": rows, "aliases": aliases})
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
	_, labels := claudePriceIdentifiers(frozenPrices.catalog)
	aliases := CloneRow(s.claudeAliases)
	s.mu.RUnlock()
	changed := false
	scope := HashString(filepath.Clean(root)) + ":"
	current := map[string]string{}
	for _, option := range ValueRows(ValueRow(settings["modelPicker"])["options"]) {
		raw := dataString(option, "model")
		if raw != "" && len(raw) <= 256 {
			label := dataString(option, "label")
			canonical := frozenPrices.model(label)
			if canonical == "" {
				canonical = labels[claudePriceLabel(label)]
			}
			current[scope+raw] = canonical
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
	aliases := s.claudeAliases
	version := s.claudePriceVersion
	s.mu.RUnlock()
	r["cost_usd"] = nil
	r["pricing_status"] = "unpriced"
	r["price_version"] = version
	r["pricing_basis"] = "official_api_reference"
	delete(r, "price_rates")
	delete(r, "pricing_mode")
	returned := dataString(r, "upstream_model")
	if returned == "" {
		return r
	}
	model := frozenPrices.model(returned)
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
					model = frozenPrices.model(dataString(history[i], "model"))
					estimated = model != ""
					break
				}
			}
		}
	}
	return frozenPrices.price(r, model, estimated, true, version)
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
func (s *Store) SetClaudePrice(model string, rates Row) error           { return officialPriceReadOnly() }
func (s *Store) syncClaudePrices(ctx context.Context, force bool) error { return nil }
