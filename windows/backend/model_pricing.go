package backend

// Both ledgers use the same frozen official seeds and approved model aliases.
import (
	_ "embed"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

//go:embed assets/model_pricing_rules.json
var modelPricingRules []byte

type modelPriceReference struct {
	rules              Row
	definitions        map[string]Row
	prices             map[string][]Row
	identifiers        map[string]string
	catalog            []Row
	display            []Row
	holidays           map[string]bool
	years              map[int]bool
	version            string
	modelFamilies      []structuredPriceFamily
	ignoredModelTokens map[string]bool
	modelNormalization []structuredPriceReplacement
}

type structuredPriceReplacement struct {
	expression  *regexp.Regexp
	replacement string
}

type structuredPriceModel struct {
	model  string
	prefix *regexp.Regexp
}
type structuredPriceFamily struct {
	anchor, shape, reject *regexp.Regexp
	generationMajor       string
	variantTokens         map[string]bool
	variantPrefixes       []string
	models                []structuredPriceModel
}

var modelStructureTokens = regexp.MustCompile(`[a-z]+|[0-9]+(?:\.[0-9]+)*`)

func modelExpression(pattern string) *regexp.Regexp {
	if pattern == "" {
		return nil
	}
	return regexp.MustCompile(pattern)
}

var modelEffortSuffix = regexp.MustCompile(`(?i)[-_\s(\[]+(minimal|low|medium|high|xhigh|max)[)\]]?\s*$`)
var modelSnapshotSuffix = regexp.MustCompile(`[-_](\d{8}|\d{4}-\d{2}-\d{2})$`)
var modelPriceChina = time.FixedZone("Asia/Shanghai", 8*3600)
var frozenPrices = newModelPriceReference()

func modelPriceKey(v string) string {
	return strings.Map(func(r rune) rune {
		if r >= 0xff01 && r <= 0xff5e {
			r -= 0xfee0
		}
		r = unicode.ToLower(r)
		if r >= 'a' && r <= 'z' || r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, v)
}
func newModelPriceReference() *modelPriceReference {
	rules, _ := DecodeRow(modelPricingRules)
	gpt, _ := DecodeRow(priceSeed)
	claude, _ := DecodeRow(claudePriceSeed)
	p := &modelPriceReference{rules: rules, definitions: map[string]Row{}, prices: map[string][]Row{}, identifiers: map[string]string{}, holidays: map[string]bool{}, years: map[int]bool{}}
	matching := ValueRow(rules["model_matching"])
	for _, raw := range ValueRows(matching["normalization"]) {
		if pattern := modelExpression(dataString(raw, "pattern")); pattern != nil {
			p.modelNormalization = append(p.modelNormalization, structuredPriceReplacement{pattern, dataString(raw, "replacement")})
		}
	}
	p.ignoredModelTokens = map[string]bool{}
	for _, token := range ValueStrings(matching["ignored_tokens"]) {
		p.ignoredModelTokens[token] = true
	}
	for _, raw := range ValueRows(matching["families"]) {
		family := structuredPriceFamily{anchor: modelExpression(dataString(raw, "anchor")), shape: modelExpression(dataString(raw, "shape")), reject: modelExpression(dataString(raw, "reject")), generationMajor: dataString(raw, "generation_major"), variantTokens: map[string]bool{}, variantPrefixes: ValueStrings(raw["variant_prefixes"])}
		if family.anchor == nil || family.shape == nil {
			continue
		}
		for _, token := range ValueStrings(raw["variant_tokens"]) {
			family.variantTokens[token] = true
		}
		for _, model := range ValueRows(raw["models"]) {
			if prefix := modelExpression(dataString(model, "prefix")); prefix != nil {
				family.models = append(family.models, structuredPriceModel{dataString(model, "model"), prefix})
			}
		}
		p.modelFamilies = append(p.modelFamilies, family)
	}
	all := append(append(ValueRows(gpt["rows"]), ValueRows(claude["rows"])...), ValueRows(rules["other_rows"])...)
	for _, r := range all {
		model := dataString(r, "model")
		p.prices[model] = append(p.prices[model], r)
	}
	for _, r := range ValueRows(rules["models"]) {
		model := dataString(r, "model")
		p.definitions[model] = r
		for _, base := range p.prices[model] {
			if ValueInt(base["threshold"]) == 0 {
				copy := CloneRow(base)
				for k, v := range r {
					copy[k] = v
				}
				if dataString(r, "group") == "other" {
					delete(copy, "cache_write_1h")
					copy["cache_write_ttl"] = "5m"
				}
				copy["service_tier"] = "default"
				copy["threshold"] = 0
				p.catalog = append(p.catalog, copy)
				allowed, limited := ValueRow(rules["client_models"])[dataString(r, "group")]
				visible := !limited
				for _, id := range ValueStrings(allowed) {
					visible = visible || id == model
				}
				if visible {
					p.display = append(p.display, copy)
				}
				break
			}
		}
	}
	ambiguous := map[string]bool{}
	for _, r := range append(all, ValueRows(rules["models"])...) {
		model := dataString(r, "model")
		names := append([]string{model, dataString(r, "display_name")}, ValueStrings(r["aliases"])...)
		for _, name := range names {
			key := modelPriceKey(name)
			if key == "" || ambiguous[key] {
				continue
			}
			if prior := p.identifiers[key]; prior != "" && prior != model {
				delete(p.identifiers, key)
				ambiguous[key] = true
			} else {
				p.identifiers[key] = model
			}
		}
	}
	calendar := ValueRow(rules["calendar"])
	for _, day := range ValueStrings(calendar["holidays"]) {
		p.holidays[day] = true
	}
	if years, ok := calendar["years"].([]any); ok {
		for _, year := range years {
			p.years[int(ValueInt(year))] = true
		}
	}
	signature := CloneRow(rules)
	signature["request_display_pricing"] = "v1"
	delete(signature, "verified_at")
	delete(signature, "client_models")
	delete(signature, "client_model_sources")
	definitions := []Row{}
	for _, r := range ValueRows(rules["models"]) {
		copy := CloneRow(r)
		for _, k := range []string{"tooltip", "tooltip_en"} {
			delete(copy, k)
		}
		definitions = append(definitions, copy)
	}
	signature["models"] = definitions
	cal := CloneRow(calendar)
	delete(cal, "source")
	signature["calendar"] = cal
	content := []Row{}
	for _, r := range all {
		copy := CloneRow(r)
		for _, k := range []string{"source", "sources", "updated_at", "verified_at", "locked"} {
			delete(copy, k)
		}
		content = append(content, copy)
	}
	otherContent := []Row{}
	for _, r := range ValueRows(rules["other_rows"]) {
		copy := CloneRow(r)
		for _, k := range []string{"source", "sources", "updated_at", "verified_at", "locked"} {
			delete(copy, k)
		}
		otherContent = append(otherContent, copy)
	}
	signature["other_rows"] = otherContent
	p.version = HashString(dataJSON(Row{"rules": signature, "prices": content, "policies": billingPolicyContent()}))
	return p
}
func (p *modelPriceReference) decoratedModels(value string) (bool, bool, map[string]bool) {
	seen, uncertain, found := false, false, map[string]bool{}
	for _, segment := range strings.FieldsFunc(value, func(r rune) bool { return strings.ContainsRune("/·|:@,;", r) }) {
		folded := strings.Map(func(r rune) rune {
			if r >= 0xff01 && r <= 0xff5e {
				return r - 0xfee0
			}
			return r
		}, segment)
		for _, rule := range p.modelNormalization {
			folded = rule.expression.ReplaceAllString(folded, rule.replacement)
		}
		folded = strings.ToLower(folded)
		tokens := []string{}
		for _, token := range modelStructureTokens.FindAllString(folded, -1) {
			if !p.ignoredModelTokens[token] {
				tokens = append(tokens, token)
			}
		}
		text := strings.Join(tokens, " ")
		type anchor struct {
			start, end int
			family     *structuredPriceFamily
		}
		anchors := []anchor{}
		for i := range p.modelFamilies {
			family := &p.modelFamilies[i]
			for _, hit := range family.anchor.FindAllStringIndex(text, -1) {
				anchors = append(anchors, anchor{hit[0], hit[1], family})
			}
		}
		sort.Slice(anchors, func(i, j int) bool { return anchors[i].start < anchors[j].start })
		for i, hit := range anchors {
			end := len(text)
			if i+1 < len(anchors) {
				end = anchors[i+1].start
			}
			body := strings.TrimSpace(text[hit.end:end])
			family := hit.family
			if !family.shape.MatchString(body) {
				continue
			}
			seen = true
			words := strings.Fields(body)
			badVersion := false
			leadingVersion := words[0]
			if leadingVersion == "v" && len(words) > 1 {
				leadingVersion = words[1]
			}
			if family.generationMajor != "" && !(leadingVersion[0] >= '0' && leadingVersion[0] <= '9') {
				for j := 0; j+1 < len(words); j++ {
					next := words[j+1]
					if words[j] == "v" && next[0] >= '0' && next[0] <= '9' && strings.SplitN(next, ".", 2)[0] != family.generationMajor {
						badVersion = true
					}
				}
			}
			if badVersion || family.reject != nil && family.reject.MatchString(body) {
				uncertain = true
				continue
			}
			models := map[string]bool{}
			for _, rule := range family.models {
				match := rule.prefix.FindStringIndex(body)
				if match == nil || match[0] != 0 {
					continue
				}
				blocked := false
				for _, token := range strings.Fields(body[match[1]:]) {
					if family.variantTokens[token] {
						blocked = true
						break
					}
					for _, prefix := range family.variantPrefixes {
						if strings.HasPrefix(token, prefix) {
							blocked = true
							break
						}
					}
					if blocked {
						break
					}
				}
				if !blocked {
					models[rule.model] = true
				}
			}
			if len(models) == 1 {
				for model := range models {
					found[model] = true
				}
			} else {
				uncertain = true
			}
		}
	}
	return seen, uncertain, found
}

func (p *modelPriceReference) model(v string) string {
	if v == "" || utf8.RuneCountInString(v) > 512 {
		return ""
	}
	original := v
	v = strings.ReplaceAll(strings.ToLower(v), "[1m]", "")
	found := map[string]bool{}
	candidates := append([]string{v}, strings.FieldsFunc(v, func(r rune) bool { return strings.ContainsRune("/·|:", r) })...)
	for _, candidate := range candidates {
		if exact := p.identifiers[modelPriceKey(candidate)]; exact != "" {
			found[exact] = true
			continue
		}
		for _, variant := range []string{candidate, modelEffortSuffix.ReplaceAllString(candidate, "")} {
			for _, name := range []string{variant, modelSnapshotSuffix.ReplaceAllString(variant, "")} {
				if model := p.identifiers[modelPriceKey(name)]; model != "" {
					found[model] = true
				}
			}
		}
	}
	seen, uncertain, decorated := p.decoratedModels(original)
	if seen {
		if uncertain {
			return ""
		}
		for model := range decorated {
			found[model] = true
		}
	}
	if len(found) == 1 {
		for model := range found {
			return model
		}
	}
	return ""
}
func (p *modelPriceReference) resolve(r Row, returnedOnly bool) (string, bool) {
	returned := []string{}
	models := map[string]bool{}
	for _, key := range []string{"upstream_model", "returned_model"} {
		if id := dataString(r, key); id != "" {
			returned = append(returned, id)
			if model := p.model(id); model != "" {
				models[model] = true
			}
		}
	}
	if len(models) == 1 {
		for model := range models {
			return model, false
		}
	}
	if len(models) > 1 || returnedOnly {
		return "", false
	}
	return p.model(dataString(r, "model")), len(returned) > 0
}
func (p *modelPriceReference) rates(model string, input int64, r Row) (Row, bool) {
	descriptor := p.definitions[model]
	family := dataString(descriptor, "group")
	var base Row
	boundary := int64(-1)
	for _, candidate := range p.prices[model] {
		threshold := ValueInt(candidate["threshold"])
		if (threshold == 0 || input > threshold) && threshold > boundary {
			base = candidate
			boundary = threshold
		}
	}
	if base == nil {
		return nil, false
	}
	rates := CloneRow(base)
	estimated := false
	tier := normalizedTier(r["service_tier"])
	if family == "gpt" {
		mode, identityEstimated, assumed := solFunding(r, true)
		policy := ValueRow(ValueRow(modelBillingPolicies[model])[mode])
		threshold := ValueInt(descriptor["long_context_threshold"])
		if len(policy) > 0 {
			threshold = ValueInt(policy["long_context_threshold"])
		}
		speed := 1.0
		if len(policy) > 0 {
			var ok bool
			speed, ok = ValueFloat(ValueRow(policy["speeds"])[tier])
			if !ok {
				return nil, false
			}
		} else if tier == "priority" || tier == "ultrafast" {
			key := "fast_multiplier"
			if tier == "ultrafast" {
				key = "ultrafast_multiplier"
			}
			var ok bool
			speed, ok = ValueFloat(descriptor[key])
			if !ok {
				return nil, false
			}
		} else if tier != "default" {
			return nil, false
		}
		long := threshold > 0 && input > threshold
		inMultiple, outMultiple := speed, speed
		if long {
			inMultiple *= 2
			outMultiple *= 1.5
		}
		writeBasis := "legacy"
		if len(policy) > 0 {
			writeBasis = firstString(policy["cache_write_basis"], "legacy")
		}
		for _, key := range rateFields {
			source := key
			if key == "cache_write" && (writeBasis == "input" || writeBasis == "legacy" && (tier != "default" || long)) {
				source = "input"
			}
			multiple := inMultiple
			if key == "output" {
				multiple = outMultiple
			}
			if v, ok := ValueFloat(base[source]); ok {
				rates[key] = v * multiple
			} else {
				rates[key] = nil
			}
		}
		rates["threshold"] = int64(0)
		if long {
			rates["threshold"] = threshold
		}
		rates["long_context_threshold"] = threshold
		rates["speed_multiplier"] = speed
		rates["input_multiplier"] = inMultiple
		rates["output_multiplier"] = outMultiple
		rates["pricing_mode"] = mode
		rates["cache_write_basis"] = writeBasis
		rates["base_rates"] = Row{}
		for _, key := range rateFields {
			ValueRow(rates["base_rates"])[key] = base[key]
		}
		rates["multipliers"] = Row{"input": inMultiple, "cache_read": inMultiple, "cache_write": inMultiple, "output": outMultiple}
		if assumed {
			rates["assumed_billing_source"] = "subscription"
		} else if identityEstimated {
			rates["pricing_identity_estimated"] = true
			estimated = true
		}
	} else if family == "claude" {
		if tier != "default" && tier != "priority" {
			return nil, false
		}
		if tier == "priority" {
			speed, ok := ValueFloat(base["fast_multiplier"])
			if !ok {
				return nil, false
			}
			for _, key := range claudePriceFields {
				if v, known := ValueFloat(base[key]); known {
					rates[key] = v * speed
				}
			}
			rates["pricing_mode"] = "credits"
		}
		estimated = dataString(r, "service_tier") == ""
	} else {
		multiple := 1.0
		if offPeak, known := ValueFloat(base["off_peak_multiplier"]); known {
			stamp, ok := ParseStamp(r["timestamp"])
			if !ok {
				return nil, false
			}
			local := stamp.In(modelPriceChina)
			minute := local.Hour()*60 + local.Minute()
			peak := local.Weekday() >= time.Monday && local.Weekday() <= time.Friday && (minute >= 540 && minute < 720 || minute >= 840 && minute < 1080) && !p.holidays[local.Format("2006-01-02")]
			if !peak {
				multiple = offPeak
			}
			estimated = peak && !p.years[local.Year()]
			rates["time_multiplier"] = multiple
		}
		if changes := ValueRows(base["price_changes"]); len(changes) > 0 {
			stamp, ok := ParseStamp(r["timestamp"])
			if !ok {
				return nil, false
			}
			for _, change := range changes {
				since, valid := ParseStamp(change["since"])
				if factor, known := ValueFloat(change["multiplier"]); valid && known && !stamp.Before(since) {
					multiple = factor
				}
			}
		}
		for _, key := range claudePriceFields {
			if v, known := ValueFloat(base[key]); known {
				rates[key] = v * multiple
			} else {
				rates[key] = nil
			}
		}
		tier = "default"
		delete(rates, "cache_write_1h")
		rates["cache_write_ttl"] = p.rules["other_cache_write_ttl"]
		rates["pricing_basis"] = "official_api_reference"
	}
	rates["model"] = model
	rates["service_tier"] = tier
	return rates, estimated
}
func (p *modelPriceReference) price(input Row, model string, aliasEstimated bool, returnedOnly bool, version string) Row {
	r := CloneRow(input)
	r["cost_usd"] = nil
	r["pricing_status"] = "unpriced"
	r["price_version"] = version
	for _, key := range []string{"price_rates", "pricing_mode", "pricing_identity_estimated", "assumed_billing_source", "pricing_reason"} {
		delete(r, key)
	}
	if name := dataString(input, "model_display_name"); name != "" {
		model, aliasEstimated = p.model(name), false
	} else if model == "" {
		model, aliasEstimated = p.resolve(input, returnedOnly)
	}
	if model == "" || ValueBool(input["usage_conflict"]) || strings.HasPrefix(dataString(input, "quality"), "invalid") {
		return r
	}
	total, ok := knownCount(input["input_tokens"])
	output, outOK := knownCount(input["output_tokens"])
	if !ok || !outOK {
		return r
	}
	count := func(key string) (int64, bool) {
		if _, exists := input[key]; !exists {
			return 0, true
		}
		return knownCount(input[key])
	}
	read, readOK := count("cached_input_tokens")
	write, writeOK := count("cache_write_input_tokens")
	if !readOK || !writeOK || read > math.MaxInt64-write {
		return r
	}
	ordinary := total - read - write
	isClaude := normalizedClient(dataString(input, "client_kind")) == "claude_code"
	if isClaude {
		var valid bool
		ordinary, valid = knownCount(input["raw_input_tokens"])
		if !valid {
			return r
		}
	}
	if ordinary < 0 || ordinary > math.MaxInt64-read-write {
		return r
	}
	contextInput := total
	if isClaude {
		contextInput = ordinary + read + write
	}
	reasoning, reasoningOK := count("reasoning_output_tokens")
	if !reasoningOK || reasoning > output {
		return r
	}
	if reported, exists := input["total_tokens"]; exists && reported != nil {
		value, valid := knownCount(reported)
		if !valid || contextInput > math.MaxInt64-output || value != contextInput+output {
			return r
		}
	}
	rates, estimated := p.rates(model, contextInput, input)
	if rates == nil {
		return r
	}
	estimated = estimated || aliasEstimated
	five, hour := write, int64(0)
	if dataString(p.definitions[model], "group") == "claude" && write > 0 && rates["cache_write_1h"] != nil {
		knownFive, okFive := knownCount(input["cache_write_5m_tokens"])
		knownHour, okHour := knownCount(input["cache_write_1h_tokens"])
		if input["cache_write_5m_tokens"] != nil && !okFive || input["cache_write_1h_tokens"] != nil && !okHour {
			return r
		}
		if okFive && okHour {
			five, hour = knownFive, knownHour
		} else if okFive {
			five, hour = knownFive, write-knownFive
		} else if okHour {
			five, hour = write-knownHour, knownHour
		} else {
			estimated = true
		}
		if five < 0 || hour < 0 || five > math.MaxInt64-hour || five+hour != write {
			return r
		}
	}
	counts := []int64{ordinary, read, five, hour, output}
	cost := 0.0
	for i, key := range claudePriceFields {
		rate, known := ValueFloat(rates[key])
		if counts[i] > 0 && !known {
			return r
		}
		cost += float64(counts[i]) * rate / 1e6
	}
	if math.IsInf(cost, 0) || math.IsNaN(cost) || cost < 0 {
		return r
	}
	family := dataString(p.definitions[model], "group")
	provider := strings.ToLower(dataString(input, "provider"))
	if family == "gpt" && provider != "openai" && provider != "codexio-upstream" || family == "other" || strings.HasPrefix(dataString(input, "quality"), "cumulative") {
		estimated = true
	}
	r["cost_usd"] = cost
	r["price_rates"] = rates
	r["pricing_status"] = "priced"
	if estimated {
		r["pricing_status"] = "estimated"
	}
	r["pricing_basis"] = "official_api_reference"
	if family == "gpt" {
		r["pricing_basis"] = "standard_api_x_codex"
	}
	for _, key := range []string{"pricing_mode", "pricing_identity_estimated", "assumed_billing_source"} {
		if v := rates[key]; v != nil {
			r[key] = v
		}
	}
	return r
}
func officialPriceReadOnly() error { return fmt.Errorf("官方价格目录为只读") }
