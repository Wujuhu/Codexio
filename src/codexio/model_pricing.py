"""Immutable official model prices; same aliases/rules as Swift and Go."""
import copy
import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

FIELDS = ("input", "cache_read", "cache_write", "cache_write_1h", "output")
_EFFORT = re.compile(r"[-_\s(\[]+(?:minimal|low|medium|high|xhigh|max)[)\]]?\s*$", re.I)
_SNAPSHOT = re.compile(r"[-_](?:\d{8}|\d{4}-\d{2}-\d{2})$")
_CHINA = timezone(timedelta(hours=8))


def _stamp(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e12 else value, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _key(value):
    return re.sub("[^a-z0-9]", "", unicodedata.normalize("NFKC", str(value)).lower())


def _tier(value):
    value = str(value or "").strip().lower()
    if value in ("", "auto", "default", "standard"):
        return "default"
    if value in ("fast", "priority"):
        return "priority"
    if value in ("ultrafast", "ultra_fast", "ultra-fast", "ultra fast"):
        return "ultrafast"
    return value


class OfficialModelPrices:
    def __init__(self):
        directory = Path(__file__).parent
        read = lambda name: json.loads((directory / name).read_text(encoding="utf-8"))
        gpt, claude, rules = (read(name) for name in ("pricing_seed.json", "claude_pricing_seed.json", "model_pricing_rules.json"))
        self.rules = rules
        self.policies = gpt.get("billing_policies", {})
        self.definitions = {row["model"]: row for row in rules["models"]}
        self.prices = {}
        self.identifiers = {}
        ambiguous = set()
        all_rows = gpt["rows"] + claude["rows"] + rules["other_rows"]
        for row in all_rows:
            self.prices.setdefault(row["model"], []).append(row)
        self.catalog_rows = []
        for descriptor in rules["models"]:
            base = next(row for row in self.prices[descriptor["model"]] if not row.get("threshold", 0))
            row = dict(base, **descriptor, service_tier="default", threshold=0)
            if descriptor["group"] == "other":
                row.pop("cache_write_1h", None)
                row["cache_write_ttl"] = rules["other_cache_write_ttl"]
            self.catalog_rows.append(row)
        self.display_rows = [row for row in self.catalog_rows if row["group"] not in rules["client_models"] or row["model"] in rules["client_models"][row["group"]]]
        for row in all_rows + rules["models"]:
            for name in [row["model"], row.get("display_name", "")] + row.get("aliases", []):
                key = _key(name)
                if not key or key in ambiguous:
                    continue
                if key in self.identifiers and self.identifiers[key] != row["model"]:
                    ambiguous.add(key)
                    self.identifiers.pop(key)
                else:
                    self.identifiers[key] = row["model"]
        self.holidays = set(rules["calendar"]["holidays"])
        self.years = set(rules["calendar"]["years"])
        content = copy.deepcopy(rules)
        content.pop("verified_at", None)
        content.pop("client_models", None)
        content.pop("client_model_sources", None)
        content["calendar"].pop("source", None)
        for row in content["models"]:
            for key in ("tooltip", "tooltip_en", "display_name"):
                row.pop(key, None)
        for row in content["other_rows"]:
            for key in ("source", "sources", "updated_at", "verified_at", "locked"):
                row.pop(key, None)
        rate_content = [{key: value for key, value in row.items() if key not in ("source", "sources", "updated_at", "verified_at", "locked")} for row in all_rows]
        policies = {model: {key: value for key, value in policy.items() if key not in ("sources", "verified_at")} for model, policy in self.policies.items()}
        self.version = hashlib.sha256(json.dumps([content, rate_content, policies], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    def model(self, value):
        if not isinstance(value, str) or not value or len(value) > 512:
            return None
        value = re.sub(r"\[1m\]", "", value, flags=re.I)
        found = set()
        for part in [value] + re.split(r"[/·|:]", value):
            for variant in (part, _EFFORT.sub("", part)):
                for candidate in (variant, _SNAPSHOT.sub("", variant)):
                    if canonical := self.identifiers.get(_key(candidate)):
                        found.add(canonical)
        return next(iter(found)) if len(found) == 1 else None

    def resolve(self, record):
        returned = [record.get(key) for key in ("upstream_model", "returned_model") if record.get(key)]
        found = {model for value in returned if (model := self.model(value))}
        if len(found) == 1:
            return next(iter(found)), False
        if len(found) > 1 or record.get("client_kind") == "claude_code":
            return None, False
        return self.model(record.get("model")), bool(returned)

    def price(self, record, funding):
        result = dict(usd=None, pricing_status="unpriced", price_version=self.version, reason="", rates={}, pricing_basis="official_api_reference")
        model, estimated = self.resolve(record)
        if not model or record.get("usage_conflict") or str(record.get("quality", "")).startswith("invalid"):
            return result
        def count(key, default=0):
            value = record.get(key, default)
            return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 and value == int(value) else None
        total, output, read, write = (count(key, None if key in ("input_tokens", "output_tokens") else 0) for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens"))
        if None in (total, output, read, write):
            return result
        ordinary = count("raw_input_tokens", None) if record.get("client_kind") == "claude_code" else total - read - write
        if ordinary is None or ordinary < 0:
            return result
        context = ordinary + read + write
        reasoning = count("reasoning_output_tokens")
        if reasoning is None or reasoning > output:
            return result
        if record.get("total_tokens") is not None and count("total_tokens", None) != context + output:
            return result
        eligible = [row for row in self.prices[model] if not row.get("threshold", 0) or context > row["threshold"]]
        rates = dict(max(eligible, key=lambda row: row.get("threshold", 0)))
        base = dict(rates)
        family, tier = self.definitions[model]["group"], _tier(record.get("service_tier"))
        if family == "gpt":
            policy = self.policies.get(model, {})
            mode, identity_estimated, assumed = funding(record, True, self.policies.get("gpt-6.1-sol", {}))
            selected = policy.get(mode, {})
            descriptor = self.definitions[model]
            speed = selected.get("speeds", {}).get(tier) if selected else 1 if tier == "default" else descriptor.get("fast_multiplier" if tier == "priority" else "ultrafast_multiplier") if tier in ("priority", "ultrafast") else None
            if speed is None:
                return result
            threshold = selected.get("long_context_threshold", 0) if selected else descriptor.get("long_context_threshold", 0)
            long = threshold > 0 and context > threshold
            im, om = speed * (2 if long else 1), speed * (1.5 if long else 1)
            basis = selected.get("cache_write_basis", "legacy")
            for key in ("input", "cache_read", "cache_write", "output"):
                source = "input" if key == "cache_write" and (basis == "input" or basis == "legacy" and (tier != "default" or long)) else key
                value = base.get(source)
                rates[key] = value * (om if key == "output" else im) if value is not None else None
            rates.update(threshold=threshold if long else 0, long_context_threshold=threshold, speed_multiplier=speed, input_multiplier=im, output_multiplier=om, pricing_mode=mode, cache_write_basis=basis, base_rates={key: base.get(key) for key in FIELDS}, multipliers=dict(input=im, cache_read=im, cache_write=im, output=om))
            if assumed:
                rates["assumed_billing_source"] = "subscription"
            elif identity_estimated:
                estimated = True
                rates["pricing_identity_estimated"] = True
            result["pricing_basis"] = "standard_api_x_codex"
        elif family == "claude":
            if tier not in ("default", "priority"):
                return result
            if tier == "priority":
                if (speed := base.get("fast_multiplier")) is None:
                    return result
                for key in FIELDS:
                    rates[key] = base[key] * speed if base.get(key) is not None else None
                rates["pricing_mode"] = "credits"
            estimated |= not record.get("service_tier")
        else:
            multiple = 1
            if "off_peak_multiplier" in base:
                if not (stamp := _stamp(record.get("timestamp"))):
                    return result
                local = stamp.astimezone(_CHINA)
                minute = local.hour * 60 + local.minute
                peak = local.weekday() < 5 and (540 <= minute < 720 or 840 <= minute < 1080) and local.date().isoformat() not in self.holidays
                multiple = 1 if peak else base["off_peak_multiplier"]
                estimated |= peak and local.year not in self.years
                rates["time_multiplier"] = multiple
            if changes := base.get("price_changes"):
                if not (stamp := _stamp(record.get("timestamp"))):
                    return result
                for change in changes:
                    if stamp >= _stamp(change["since"]):
                        multiple = change["multiplier"]
            for key in FIELDS:
                rates[key] = base[key] * multiple if base.get(key) is not None else None
            rates.pop("cache_write_1h", None)
            rates["cache_write_ttl"] = self.rules["other_cache_write_ttl"]
            tier = "default"
        five, hour = write, 0
        if family == "claude" and write and rates.get("cache_write_1h") is not None:
            k5, kh = count("cache_write_5m_tokens", None), count("cache_write_1h_tokens", None)
            if record.get("cache_write_5m_tokens") is not None and k5 is None or record.get("cache_write_1h_tokens") is not None and kh is None:
                return result
            if k5 is not None and kh is not None:
                five, hour = k5, kh
            elif k5 is not None:
                five, hour = k5, write - k5
            elif kh is not None:
                five, hour = write - kh, kh
            else:
                estimated = True
            if five < 0 or hour < 0 or five + hour != write:
                return result
        amounts = (ordinary, read, five, hour, output)
        if any(n and rates.get(key) is None for key, n in zip(FIELDS, amounts)):
            return result
        cost = math.fsum(n * (rates.get(key) or 0) / 1e6 for key, n in zip(FIELDS, amounts))
        if not math.isfinite(cost):
            return result
        estimated |= family == "other" or family == "gpt" and str(record.get("provider", "")).lower() not in ("openai", "codexio-upstream") or str(record.get("quality", "")).startswith("cumulative")
        rates.update(model=model, service_tier=tier)
        result.update(usd=cost, pricing_status="estimated" if estimated else "priced", rates=rates)
        for key in ("pricing_mode", "pricing_identity_estimated", "assumed_billing_source"):
            if key in rates:
                result[key] = rates[key]
        return result


OFFICIAL_PRICES = OfficialModelPrices()
