"""Standard API base prices with one shared Codex conversion policy.

Sources: https://developers.openai.com/api/docs/pricing
https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
https://models.dev/api.json
All rates in the persisted/UI representation are USD per million tokens.
"""
from __future__ import annotations

from codexio.i18n import tr

import hashlib
import copy
import json
import math
import re
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from .model_pricing import OFFICIAL_PRICES

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
MODELS_DEV_URL = "https://models.dev/api.json"
RATE_KEYS = ("input", "cache_read", "cache_write", "output")
_FIELDS = {"input": "input_cost_per_token", "cache_read": "cache_read_input_token_cost",
           "cache_write": "cache_creation_input_token_cost", "output": "output_cost_per_token"}
PRICING_RULE_VERSION = "codex-api-base-2026-10-29-v9-terra-ultrafast"
PRICING_BASIS = "standard_api_x_codex"
PRICING_BASIS_LABEL = tr("标准 API 单价 × Codex 倍率")
LONG_CONTEXT_THRESHOLD = 272000
RULE_SOURCES = (
    "https://learn.chatgpt.com/docs/agent-configuration/speed",
    "https://help.openai.com/en/articles/20001415-chatgpt-rate-card-enterprise-token-based-pricing",
)


def codex_policy(model):
    """Known Codex modifiers; unsupported Fast modes are not inferred from API tiers."""
    model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model.removeprefix("openai/"))
    if model in ("gpt-6.1-sol", "gpt-6.1-terra"):
        return 0, 2.5
    if model in ("gpt-6-sol", "gpt-6-luna"):
        return LONG_CONTEXT_THRESHOLD, 2.5
    if model in ("gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"):
        return LONG_CONTEXT_THRESHOLD, 2.5
    if model == "gpt-6-astra":
        return 0, 2.5
    if model == "gpt-5.5":
        return LONG_CONTEXT_THRESHOLD, 2.5
    if model == "gpt-5.4":
        return LONG_CONTEXT_THRESHOLD, 2.0
    return 0, None


def _is_standard_base(row):
    return row.get("service_tier", "default") in ("default", "standard") and row.get("threshold", 0) == 0


def _scaled(value, multiplier):
    return None if value is None else float(Decimal(str(value)) * Decimal(str(multiplier)))


def _pricing_text(value):
    return value.strip().lower().replace("-", "_") if isinstance(value, str) else ""


_API_MODES = {"api_key", "apikey", "api", "openai_api_key"}
_CHATGPT_MODES = {"chatgpt", "chatgpt_auth_tokens", "chatgptauthtokens", "chatgpt_subscription", "chatgpt_oauth"}
_SUBSCRIPTION_SOURCES = {"subscription", "subscription_allowance", "subscription_quota", "chatgpt_subscription", "included", "included_usage", "plan_allowance"}
_CREDIT_SOURCES = {"credits", "credit", "paid_credits", "purchased_credits", "codex_credits", "prepaid", "pay_as_you_go", "payg"}


def _pricing_identity(record, official, policy):
    """Only explicit request evidence selects billing; never consult live accounts."""
    auth = _pricing_text(record.get("auth_mode"))
    if auth in _API_MODES:
        return "api_key", False, False
    for key in ("billing_source", "usage_source"):
        source = _pricing_text(record.get(key))
        if source in _API_MODES:
            return "api_key", auth not in _API_MODES, False
        if source in _SUBSCRIPTION_SOURCES:
            return "subscription", auth not in _CHATGPT_MODES, False
        if source in _CREDIT_SOURCES:
            return "credits", auth not in _CHATGPT_MODES, False
    mode = policy.get("unknown_official_mode", "legacy") if official else "legacy"
    return (mode if mode in ("subscription", "legacy") else "unknown"), (policy.get("unknown_official_estimated", True) if official else True), True


def _policy_content(policies):
    # Provenance and verification dates do not change numerical pricing rules.
    return {model: {key: value for key, value in policy.items()
                    if key not in ("sources", "verified_at")}
            for model, policy in policies.items() if isinstance(policy, dict)}


def _sol_rates(base, policy, mode, tier, input_tokens):
    rule = policy.get(mode, {})
    speed = rule.get("speeds", {}).get(tier)
    if speed is None:
        return None
    boundary = rule.get("long_context_threshold", 0)
    threshold = boundary if input_tokens > boundary > 0 else 0
    in_multiplier = speed * (2 if threshold else 1)
    out_multiplier = speed * (1.5 if threshold else 1)
    write_basis = rule.get("cache_write_basis", "api")
    if write_basis == "legacy":
        write_basis = "input" if tier != "default" or threshold else "api"
    row = dict(base, service_tier=tier, threshold=threshold,
               long_context_threshold=boundary, pricing_mode=mode,
               speed_multiplier=speed, pricing_basis=PRICING_BASIS,
               rule_version=PRICING_RULE_VERSION)
    for key in RATE_KEYS:
        source_key = "input" if key == "cache_write" and write_basis == "input" else key
        row[key] = _scaled(base.get(source_key), out_multiplier if key == "output" else in_multiplier)
    row["base_rates"] = {key: base.get(key) for key in RATE_KEYS}
    row["multipliers"] = {key: out_multiplier if key == "output" else in_multiplier for key in RATE_KEYS}
    row["cache_write_basis"] = write_basis
    row["cache_write_surcharge"] = (None if base.get("cache_write") is None else max(0, base["cache_write"]-base["input"])) if write_basis == "api" else 0.0
    row["condition"] = {"default": "Standard", "priority": "Fast", "ultrafast": "Ultra Fast"}.get(tier, tier)
    return row


def _codex_rows(base, policies=None):
    model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", base["model"].removeprefix("openai/"))
    policy = (policies or {}).get(model, {})
    if policy:
        for tier in policy.get("subscription", {}).get("speeds", {}):
            row = _sol_rates(base, policy, "subscription", tier, 0)
            if row:
                yield row
        return
    threshold, fast = codex_policy(base["model"])
    contexts = (0, threshold) if threshold else (0,)
    tiers = (("default", 1.0), ("priority", fast)) if fast is not None else (("default", 1.0),)
    for tier, speed in tiers:
        for context in contexts:
            input_multiplier = speed * (2.0 if context else 1.0)
            output_multiplier = speed * (1.5 if context else 1.0)
            api_standard = tier == "default" and context == 0
            write_rate = base["cache_write"] if api_standard else base["input"]
            yield dict(base, service_tier=tier, threshold=context,
                       input=_scaled(base["input"], input_multiplier),
                       cache_read=_scaled(base["cache_read"], input_multiplier),
                       cache_write=_scaled(write_rate, input_multiplier), output=_scaled(base["output"], output_multiplier),
                       base_rates={key: base[key] for key in RATE_KEYS},
                       multipliers={"input": input_multiplier, "cache_read": input_multiplier,
                                    "cache_write": input_multiplier, "output": output_multiplier},
                       cache_write_basis="api" if api_standard else "input",
                       cache_write_surcharge=(None if write_rate is None else max(0, write_rate - base["input"])) if api_standard else 0.0,
                       fast_multiplier=speed, long_context_threshold=threshold,
                       pricing_basis=PRICING_BASIS, rule_version=PRICING_RULE_VERSION,
                       rule_sources=list(RULE_SOURCES))


def _utcnow():
    return datetime.now(timezone.utc)


def _read(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _row_key(row):
    return row["model"], row["service_tier"], row["threshold"]


def _validate_row(row):
    if not isinstance(row, dict) or not isinstance(row.get("model"), str) or not row["model"].strip():
        return None
    rates = {key: _number(row.get(key)) for key in RATE_KEYS}
    if rates["input"] is None or rates["output"] is None:
        return None
    threshold = _number(row.get("threshold", 0))
    if threshold is None or int(threshold) != threshold:
        return None
    return dict(rates, model=row["model"].strip(), service_tier=str(row.get("service_tier", "default")),
                threshold=int(threshold), source=str(row.get("source", "")),
                updated_at=str(row.get("updated_at", "")), locked=bool(row.get("locked", False)))


def _price_signature(rows, billing_policies=None):
    # Fetch timestamps do not change the price version when the prices did not change.
    values = [{k: v for k, v in row.items() if k != "updated_at"} for row in rows]
    return hashlib.sha256(json.dumps([PRICING_RULE_VERSION, values, _policy_content(billing_policies or {})], sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()[:16]


def _litellm_rows(data, updated_at):
    rows = []
    if not isinstance(data, dict):
        return rows
    for model, item in data.items():
        if not isinstance(item, dict) or item.get("litellm_provider") != "openai":
            continue
        if item.get("mode") not in ("chat", "responses", "completion"):
            continue
        model = model.removeprefix("openai/")
        if "/" in model or model.startswith("ft:"):
            continue
        row = {key: _scaled(_number(item.get(field)), 1_000_000) for key, field in _FIELDS.items()}
        row.update(model=model, service_tier="default", threshold=0,
                   source=LITELLM_URL, updated_at=updated_at, locked=False)
        valid = _validate_row(row)
        if valid:
            rows.append(valid)
    return rows


def _models_dev_rows(data, updated_at):
    # Upstream context and speed prices never enter the Codex conversion catalog.
    rows = []
    provider = data.get("openai", {}) if isinstance(data, dict) else {}
    models = provider.get("models", {}) if isinstance(provider, dict) else {}
    if not isinstance(models, dict):
        return rows
    for model, item in models.items():
        if not isinstance(item, dict) or not isinstance(item.get("cost"), dict):
            continue
        cost = item["cost"]
        row = {key: cost.get(key) for key in RATE_KEYS}
        row.update(model=model, service_tier="default", threshold=0,
                   source=MODELS_DEV_URL, updated_at=updated_at, locked=False)
        valid = _validate_row(row)
        if valid:
            rows.append(valid)
    return rows


def _conflicting_models(primary, reference):
    references = {_row_key(row): row for row in reference}
    conflicts = set()
    for row in primary:
        if row["service_tier"] != "default":
            continue
        other = references.get(_row_key(row))
        if other and any(row[key] is not None and other[key] is not None
                         and not math.isclose(row[key], other[key], rel_tol=1e-6, abs_tol=1e-9)
                         for key in RATE_KEYS):
            conflicts.add(row["model"])
    return conflicts


class PricingCatalog:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self._lock = threading.RLock()
        self.status = {}
        self._cache = {}
        self._overrides = {}
        self._rows = []
        self.load()

    def load(self):
        with self._lock:
            self._seed = OFFICIAL_PRICES.display_rows
            self._billing_policies = OFFICIAL_PRICES.policies
            self._cache = {}
            self._overrides = {}
            self._rebuild()
            self.status = {"status":"bundled", "updated_at":OFFICIAL_PRICES.rules["verified_at"], "error":None, "changed":False, "price_version":self.price_version}
            try:
                self._archive_version()
            except OSError as exc:
                self.status["warning"] = str(exc)[:160]
            return self

    def _rebuild(self):
        self._base_rows = copy.deepcopy(OFFICIAL_PRICES.display_rows)
        self._standard_rows = copy.deepcopy(self._base_rows)
        self._rows = copy.deepcopy(self._base_rows)
        self.price_version = OFFICIAL_PRICES.version

    def _archive_version(self):
        """A content-addressed version is written once, including manual prices."""
        path = self.cache_dir / "versions" / (self.price_version + ".json")
        if not path.exists():
            _write(path, {"price_version": self.price_version, "created_at": _utcnow().isoformat(),
                          "unit": "USD per million tokens", "pricing_basis": PRICING_BASIS,
                          "rule_version": PRICING_RULE_VERSION,
                          "standard_rows": self.standard_rows(), "rows": self.rows()})

    def standard_rows(self):
        with self._lock:
            return [dict(row) for row in self._standard_rows]

    def rows(self):
        with self._lock:
            return copy.deepcopy(self._rows)

    def set_override(self, model: str, rates: dict | None):
        raise ValueError(tr("官方价格目录为只读"))

    def price(self, record: dict):
        with self._lock:
            return self._price(record)

    def _price(self, record):
        return OFFICIAL_PRICES.price(record, _pricing_identity)

    def _fetch(self, url, etag=None):
        headers = {"Accept": "application/json", "User-Agent": "Codexio/2 pricing"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            with urlopen(Request(url, headers=headers), timeout=15) as response:
                # Bound an unexpected upstream response; feeds currently fit comfortably.
                payload = response.read(25_000_001)
                if len(payload) > 25_000_000:
                    raise ValueError(tr("价格目录超过大小限制"))
                return json.loads(payload.decode("utf-8")), response.headers.get("ETag"), False
        except HTTPError as exc:
            if exc.code == 304:
                return None, etag, True
            raise

    def sync(self, *, force=False):
        with self._lock:
            self.status.update(changed=False, price_version=self.price_version)
            return dict(self.status)
