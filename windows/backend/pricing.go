package backend

import (
	_ "embed"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

//go:embed assets/pricing_seed.json
var priceSeed []byte

const priceRule = "codex-api-base-2026-09-30-v6-gpt-6.1-sol-codex-multipliers"

var rateFields = []string{"input", "cache_read", "cache_write", "output"}
var tokenFields = []string{"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"}
var datedModel = regexp.MustCompile(`-\d{4}-\d{2}-\d{2}$`)

func normalizedTier(v any) string {
	switch strings.ToLower(ValueString(v)) {
	case "", "auto", "default", "standard":
		return "default"
	case "priority", "fast":
		return "priority"
	default:
		return "unknown"
	}
}
func knownCount(v any) (int64, bool) {
	switch x := v.(type) {
	case int64:
		return x, x >= 0
	case int:
		return int64(x), x >= 0
	case json.Number:
		if n, e := x.Int64(); e == nil {
			return n, n >= 0
		}
	case string:
		if n, e := strconv.ParseInt(x, 10, 64); e == nil {
			return n, n >= 0
		}
	}
	n, ok := ValueFloat(v)
	return int64(n), ok && n >= 0 && n < math.MaxInt64 && math.Trunc(n) == n
}
func codexPolicy(model string) (int64, float64) {
	model = datedModel.ReplaceAllString(strings.TrimPrefix(model, "openai/"), "")
	switch model {
	case "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5":
		return 272000, 2.5
	case "gpt-6-astra":
		return 0, 2.5
	case "gpt-5.4":
		return 272000, 2
	}
	return 0, 0
}
func (s *Store) priceDirectory() string {
	name := "prices"
	if s.options.Mock {
		name = "mock_prices"
	}
	return filepath.Join(s.options.Directory, name)
}
func validBase(r Row) bool {
	if dataString(r, "model") == "" || ValueInt(r["threshold"]) != 0 || normalizedTier(r["service_tier"]) != "default" {
		return false
	}
	for _, k := range rateFields {
		n, ok := ValueFloat(r[k])
		if !ok {
			if k == "input" || k == "output" || r[k] != nil {
				return false
			}
		} else if n < 0 {
			return false
		}
	}
	return true
}
func (s *Store) loadPrices() error {
	seed, _ := DecodeRow(priceSeed)
	cached, _ := ReadJSON(filepath.Join(s.priceDirectory(), "pricing_cache.json"))
	overrides, _ := ReadJSON(filepath.Join(s.priceDirectory(), "pricing_overrides.json"))
	bases := map[string]Row{}
	for _, r := range append(ValueRows(seed["rows"]), ValueRows(cached["rows"])...) {
		if !validBase(r) {
			continue
		}
		model := dataString(r, "model")
		prior := bases[model]
		for _, k := range rateFields {
			if r[k] == nil && prior[k] != nil {
				r[k] = prior[k]
			}
		}
		bases[model] = CloneRow(r)
	}
	// These newly verified official rates win over stale third-party caches.
	for _, r := range ValueRows(seed["rows"]) {
		if dataString(r, "model") == "gpt-6.1-sol" && validBase(r) {
			bases[dataString(r, "model")] = CloneRow(r)
		}
	}
	for model, rows := range overrides {
		for _, r := range ValueRows(rows) {
			if validBase(r) {
				r["model"] = model
				r["locked"] = true
				bases[model] = CloneRow(r)
			}
		}
	}
	result := []Row{}
	models := make([]string, 0, len(bases))
	for model := range bases {
		models = append(models, model)
	}
	sort.Strings(models)
	for _, model := range models {
		base := bases[model]
		threshold, fast := codexPolicy(model)
		contexts := []int64{0}
		if threshold > 0 {
			contexts = append(contexts, threshold)
		}
		speeds := []float64{1}
		if fast > 0 {
			speeds = append(speeds, fast)
		}
		for i, speed := range speeds {
			for _, boundary := range contexts {
				r := CloneRow(base)
				inMul, outMul := speed, speed
				if boundary > 0 {
					inMul *= 2
					outMul *= 1.5
				}
				r["base_rates"] = Row{}
				for _, k := range rateFields {
					ValueRow(r["base_rates"])[k] = base[k]
					rate := base[k]
					mul := inMul
					if k == "output" {
						mul = outMul
					}
					if k == "cache_write" && (i > 0 || boundary > 0) {
						rate = base["input"]
					}
					if n, ok := ValueFloat(rate); ok {
						r[k] = n * mul
					} else {
						r[k] = nil
					}
				}
				r["service_tier"] = "default"
				if i > 0 {
					r["service_tier"] = "priority"
				}
				r["threshold"] = boundary
				r["condition"] = "Standard"
				if i > 0 {
					r["condition"] = "Codex Fast"
				}
				if boundary > 0 {
					r["condition"] = dataString(r, "condition") + " · >272K input"
				}
				r["pricing_basis"] = "standard_api_x_codex"
				r["rule_version"] = priceRule
				result = append(result, r)
			}
		}
	}
	content := make([]Row, len(result))
	for i, r := range result {
		content[i] = Row{}
		for _, k := range append([]string{"model", "service_tier", "threshold"}, rateFields...) {
			content[i][k] = r[k]
		}
	}
	version := HashString(priceRule + dataJSON(content))[:16]
	s.mu.Lock()
	s.prices = result
	s.priceVersion = version
	s.priceStatus = Row{"status": firstString(cached["status"], "bundled"), "updated_at": cached["updated_at"], "error": cached["error"]}
	s.mu.Unlock()
	archive := filepath.Join(s.priceDirectory(), "versions", version+".json")
	if _, e := os.Stat(archive); os.IsNotExist(e) {
		if e = WriteJSON(archive, Row{"price_version": version, "created_at": UTCStamp(time.Now()), "rule_version": priceRule, "pricing_basis": "standard_api_x_codex", "rows": result}); e != nil {
			return e
		}
	}
	return nil
}
func (s *Store) Prices() []Row {
	s.mu.RLock()
	defer s.mu.RUnlock()
	r := make([]Row, len(s.prices))
	for i, p := range s.prices {
		r[i] = CloneRow(p)
	}
	return r
}
func (s *Store) SetPrice(model string, rates Row) error {
	s.work.Lock()
	defer s.work.Unlock()
	model = strings.TrimSpace(model)
	if model == "" || len(model) > 160 {
		return errors.New("invalid model")
	}
	path := filepath.Join(s.priceDirectory(), "pricing_overrides.json")
	overrides, _ := ReadJSON(path)
	if len(rates) == 0 {
		delete(overrides, model)
	} else {
		r := Row{"model": model, "service_tier": "default", "threshold": 0, "locked": true, "source": "manual"}
		for _, k := range rateFields {
			r[k] = rates[k]
		}
		if !validBase(r) || ValueInt(rates["threshold"]) != 0 || normalizedTier(rates["service_tier"]) != "default" {
			return errors.New("edit finite nonnegative Standard base prices only; input and output are required")
		}
		overrides[model] = []Row{r}
	}
	if e := WriteJSON(path, overrides); e != nil {
		return e
	}
	if e := s.loadPrices(); e != nil {
		return e
	}
	s.Refresh()
	return nil
}
func (s *Store) price(r Row) Row {
	r = CloneRow(r)
	r["cost_usd"] = nil
	r["pricing_status"] = "unpriced"
	r["price_version"] = s.priceVersion
	r["pricing_basis"] = "standard_api_x_codex"
	counts := map[string]int64{}
	for _, k := range tokenFields {
		v, exists := r[k]
		if !exists && k != "input_tokens" && k != "output_tokens" && k != "total_tokens" {
			v = 0
		}
		n, ok := knownCount(v)
		if !ok {
			r["pricing_status"] = "invalid"
			r["pricing_reason"] = "缺少有效 Token 分项"
			return r
		}
		counts[k] = n
	}
	if counts["input_tokens"]+counts["output_tokens"] != counts["total_tokens"] || counts["cached_input_tokens"]+counts["cache_write_input_tokens"] > counts["input_tokens"] || counts["reasoning_output_tokens"] > counts["output_tokens"] || strings.HasPrefix(dataString(r, "quality"), "invalid") {
		r["pricing_status"] = "invalid"
		r["pricing_reason"] = "Token 分项不一致"
		return r
	}
	provider := strings.ToLower(dataString(r, "provider"))
	if provider == "" || provider == "unknown" {
		r["pricing_reason"] = "模型供应商未记录"
		return r
	}
	model := dataString(r, "model")
	official := provider == "openai" || provider == "codexio-upstream"
	if official {
		model = strings.TrimPrefix(model, "openai/")
	}
	tier := normalizedTier(r["service_tier"])
	var selected Row
	// API-key records must explicitly retain their authentication mode; Codex modifiers do not apply.
	api := dataString(r, "auth_mode") == "api_key" || dataString(r, "auth_mode") == "apikey"
	for _, p := range s.prices {
		if dataString(p, "model") != model || dataString(p, "service_tier") != tier {
			continue
		}
		threshold := ValueInt(p["threshold"])
		if api && threshold > 0 {
			continue
		}
		if threshold == 0 || counts["input_tokens"] > threshold {
			if selected == nil || threshold > ValueInt(selected["threshold"]) {
				selected = p
			}
		}
	}
	if api && tier != "default" {
		selected = nil
	}
	if selected == nil {
		r["pricing_reason"] = "缺少该模型与服务档位的明确计价规则"
		return r
	}
	parts := map[string]int64{"input": counts["input_tokens"] - counts["cached_input_tokens"] - counts["cache_write_input_tokens"], "cache_read": counts["cached_input_tokens"], "cache_write": counts["cache_write_input_tokens"], "output": counts["output_tokens"]}
	cost := 0.0
	for k, n := range parts {
		rate, ok := ValueFloat(selected[k])
		if n > 0 && !ok {
			r["pricing_reason"] = "缺少已使用 Token 类别的明确价格"
			return r
		}
		cost += float64(n) * rate / 1e6
	}
	r["cost_usd"] = cost
	r["pricing_status"] = "priced"
	if !official || dataString(r, "service_tier") == "" || strings.HasPrefix(dataString(r, "quality"), "cumulative") {
		r["pricing_status"] = "estimated"
	}
	r["pricing_reason"] = "标准 API 基础价格及独立 Codex 换算规则"
	r["rates"] = selected
	return r
}
