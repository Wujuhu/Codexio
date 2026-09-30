package backend

// Display only. Historical/API rates and their pricing signature stay intact.
// The current Codex selector order is explicitly supplied by the user.
import (
	"sort"
	"strings"
)

var codexModelOrder = []string{"gpt-6.1-sol", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5"}

func codexDisplayPrices(prices []Row) []Row {
	groups := map[string][]Row{}
	for _, price := range prices {
		model := strings.TrimPrefix(strings.ToLower(ValueString(price["model"])), "openai/")
		for _, allowed := range codexModelOrder {
			if model == allowed {
				groups[allowed] = append(groups[allowed], price)
				break
			}
		}
	}
	result := []Row{}
	for _, model := range codexModelOrder {
		group := groups[model]
		if len(group) == 0 {
			result = append(result, Row{"model": model, "input": nil, "cache_read": nil, "cache_write": nil, "output": nil, "service_tier": "default", "threshold": 0, "condition": "Standard", "pricing_status": "unpriced"})
			continue
		}
		sort.SliceStable(group, func(i, j int) bool {
			a, b := ValueInt(group[i]["threshold"]), ValueInt(group[j]["threshold"])
			if a != b {
				return a < b
			}
			return normalizedTier(group[i]["service_tier"]) == "default" && normalizedTier(group[j]["service_tier"]) != "default"
		})
		result = append(result, group...)
	}
	return result
}
