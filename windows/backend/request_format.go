package backend

import (
	"regexp"
	"strings"
)

var requestGPTName = regexp.MustCompile(`(?i)gpt-`)
var requestModelSuffix = regexp.MustCompile(`(?i)-(astra|sol|luna|terra)\b`)

// Match Core.swift/modelName and the frontend's logFormat without changing IDs.
func requestModelName(value any) string {
	name := strings.TrimSpace(ValueString(value))
	if name == "" {
		return "—"
	}
	name = requestGPTName.ReplaceAllString(name, "GPT-")
	return requestModelSuffix.ReplaceAllStringFunc(name, func(suffix string) string {
		word := strings.ToLower(suffix[1:])
		return " " + strings.ToUpper(word[:1]) + word[1:]
	})
}

func requestEffortName(value any) string {
	raw := strings.TrimSpace(ValueString(value))
	switch strings.ToLower(raw) {
	case "none", "无":
		return "None"
	case "minimal", "最轻":
		return "Minimal"
	case "low", "轻度":
		return "Low"
	case "medium", "中等":
		return "Medium"
	case "high", "高度":
		return "High"
	case "xhigh", "extra high", "extra_high", "extra-high", "极高":
		return "Extra High"
	case "max", "最高":
		return "Max"
	case "ultra", "超高":
		return "Ultra"
	case "unknown", "未知":
		return "Unknown"
	default:
		return raw
	}
}

// Request metadata is evidence, not the pricing fallback in normalizedTier.
func requestSpeed(value any) string {
	switch strings.ToLower(strings.TrimSpace(ValueString(value))) {
	case "default", "standard":
		return "default"
	case "priority", "fast":
		return "priority"
	case "ultrafast", "ultra_fast", "ultra-fast":
		return "ultrafast"
	case "mixed":
		return "mixed"
	default:
		return "unknown"
	}
}
