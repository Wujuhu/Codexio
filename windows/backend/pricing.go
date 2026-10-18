package backend

import (
	_ "embed"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

//go:embed assets/pricing_seed.json
var priceSeed []byte

const priceRule = "codex-api-base-2026-10-13-v7-gpt-6.1-sol-funding"

var solBillingPolicy = func() Row {
	seed, _ := DecodeRow(priceSeed)
	return ValueRow(ValueRow(seed["billing_policies"])["gpt-6.1-sol"])
}()

var rateFields = []string{"input", "cache_read", "cache_write", "output"}
var tokenFields = []string{"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"}
var datedModel = regexp.MustCompile(`-\d{4}-\d{2}-\d{2}$`)

func normalizedTier(v any) string {
	switch strings.ToLower(strings.TrimSpace(ValueString(v))) {
	case "", "auto", "default", "standard":
		return "default"
	case "priority", "fast":
		return "priority"
	case "ultrafast", "ultra_fast", "ultra-fast", "ultra fast":
		return "ultrafast"
	default:
		return "unknown"
	}
}

func pricingIdentity(value any) string {
	text, _ := value.(string)
	return strings.ReplaceAll(strings.ToLower(strings.TrimSpace(text)), "-", "_")
}

func solFunding(record Row, official bool) (string, bool, bool) {
	// Authentication evidence wins over a contradictory allowance marker.
	authKnown := false
	switch pricingIdentity(record["auth_mode"]) {
	case "api", "api_key", "apikey", "openai_api_key":
		return "api_key", false, false
	case "chatgpt", "chatgpt_auth_tokens", "chatgptauthtokens", "chatgpt_subscription", "chatgpt_oauth":
		authKnown = true
	}
	for _, key := range []string{"billing_source", "usage_source"} {
		switch pricingIdentity(record[key]) {
		case "api", "api_key", "apikey", "openai_api_key":
			return "api_key", true, false
		case "subscription", "subscription_allowance", "subscription_quota", "chatgpt_subscription", "included", "included_usage", "plan_allowance":
			return "subscription", !authKnown, false
		case "credits", "credit", "paid_credits", "purchased_credits", "codex_credits", "prepaid", "pay_as_you_go", "payg":
			return "credits", !authKnown, false
		}
	}
	if official {
		mode := dataString(solBillingPolicy, "unknown_official_mode")
		if (mode == "subscription" || mode == "legacy") && len(ValueRow(solBillingPolicy[mode])) > 0 {
			estimated := true
			if recorded, ok := solBillingPolicy["unknown_official_estimated"].(bool); ok {
				estimated = recorded
			}
			return mode, estimated, true
		}
	}
	return "legacy", true, true
}

func solPolicyContent() Row {
	content := Row{"rule_version": solBillingPolicy["rule_version"], "unknown_official_mode": solBillingPolicy["unknown_official_mode"], "unknown_official_estimated": solBillingPolicy["unknown_official_estimated"]}
	for _, mode := range []string{"subscription", "api_key", "credits", "legacy"} {
		policy := ValueRow(solBillingPolicy[mode])
		content[mode] = Row{"long_context_threshold": policy["long_context_threshold"], "speeds": policy["speeds"], "cache_write_basis": policy["cache_write_basis"]}
	}
	return content
}

func solPriceRates(base Row, mode, tier string, input int64) Row {
	policy := ValueRow(solBillingPolicy[mode])
	speed, ok := ValueFloat(ValueRow(policy["speeds"])[tier])
	if !ok || speed <= 0 || math.IsNaN(speed) || math.IsInf(speed, 0) {
		return nil
	}
	threshold := ValueInt(policy["long_context_threshold"])
	long := threshold > 0 && input > threshold
	inMultiplier, outMultiplier := speed, speed
	if long {
		inMultiplier *= 2
		outMultiplier *= 1.5
	}
	rates := CloneRow(base)
	rates["base_rates"] = Row{}
	for _, key := range rateFields {
		ValueRow(rates["base_rates"])[key] = base[key]
		value, multiplier := base[key], inMultiplier
		if key == "output" {
			multiplier = outMultiplier
		}
		if key == "cache_write" {
			basis := dataString(policy, "cache_write_basis")
			if basis == "input" || basis == "legacy" && (tier != "default" || long) {
				value = base["input"]
			}
		}
		if n, valid := ValueFloat(value); valid {
			rates[key] = n * multiplier
		} else {
			rates[key] = nil
		}
	}
	rates["service_tier"] = tier
	rates["condition"] = map[string]string{"default": "Standard", "priority": "Fast", "ultrafast": "Ultra Fast"}[tier]
	rates["threshold"] = int64(0)
	if long {
		rates["threshold"] = threshold
	}
	rates["pricing_mode"] = mode
	rates["long_context_threshold"] = threshold
	rates["speed_multiplier"] = speed
	rates["multipliers"] = Row{"input": inMultiplier, "cache_read": inMultiplier, "cache_write": inMultiplier, "output": outMultiplier}
	basis := dataString(policy, "cache_write_basis")
	if basis == "legacy" {
		basis = "api"
		if tier != "default" || long {
			basis = "input"
		}
	}
	rates["cache_write_basis"] = basis
	rates["rule_version"] = priceRule
	rates["pricing_basis"] = "standard_api_x_codex"
	return rates
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
	if e := s.loadClaudePrices(); e != nil {
		return e
	}
	s.mu.Lock()
	s.prices = frozenPrices.display
	s.codexPriceVersion = frozenPrices.version
	s.priceVersion = HashString(frozenPrices.version + "|" + s.claudePriceVersion)
	s.priceStatus = Row{"status": "bundled", "updated_at": frozenPrices.rules["verified_at"]}
	s.mu.Unlock()
	archive := filepath.Join(s.priceDirectory(), "versions", frozenPrices.version+".json")
	if _, e := os.Stat(archive); os.IsNotExist(e) {
		return WriteJSON(archive, Row{"price_version": frozenPrices.version, "rows": frozenPrices.catalog, "rules": frozenPrices.rules, "billing_policies": Row{"gpt-6.1-sol": solBillingPolicy}})
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
func (s *Store) SetPrice(model string, rates Row) error { return officialPriceReadOnly() }
func (s *Store) price(r Row) Row {
	r = CloneRow(r)
	if normalizedClient(dataString(r, "client_kind")) == "claude_code" {
		r["cost_usd"] = nil
		r["pricing_status"] = "not_applicable"
		for _, field := range ValueStrings(r["final_missing_fields"]) {
			r[field] = nil
		}
		r["context_input_tokens"] = r["input_tokens"]
		if r["input_complete"] == false {
			r["input_tokens"] = nil
			r["context_input_tokens"] = nil
		}
		if !ValueBool(r["usage_complete"]) {
			r["total_tokens"] = nil
		}
		if ValueBool(r["usage_conflict"]) {
			for _, field := range tokenFields {
				r[field] = nil
			}
			r["context_input_tokens"] = nil
		}
		return s.priceClaude(r)
	}
	s.mu.RLock()
	version := s.priceVersion
	s.mu.RUnlock()
	return frozenPrices.price(r, "", false, false, version)
}
