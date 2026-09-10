"""Standard API base prices with one shared Codex conversion policy.

Sources: https://developers.openai.com/api/docs/pricing
https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
https://models.dev/api.json
All rates in the persisted/UI representation are USD per million tokens.
"""
from __future__ import annotations

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

LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
MODELS_DEV_URL = "https://models.dev/api.json"
RATE_KEYS = ("input", "cache_read", "cache_write", "output")
_FIELDS = {"input": "input_cost_per_token", "cache_read": "cache_read_input_token_cost",
           "cache_write": "cache_creation_input_token_cost", "output": "output_cost_per_token"}
PRICING_RULE_VERSION = "codex-api-base-2026-09-10-v1"
PRICING_BASIS = "standard_api_x_codex"
PRICING_BASIS_LABEL = "标准 API 单价 × Codex 倍率"
LONG_CONTEXT_THRESHOLD = 272000
RULE_SOURCES = (
    "https://learn.chatgpt.com/docs/agent-configuration/speed",
    "https://help.openai.com/en/articles/20001415-chatgpt-rate-card-enterprise-token-based-pricing",
)


def codex_policy(model):
    """Known Codex modifiers; unsupported Fast modes are not inferred from API tiers."""
    model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model.removeprefix("openai/"))
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


def _codex_rows(base):
    threshold, fast = codex_policy(base["model"])
    contexts = (0, threshold) if threshold else (0,)
    tiers = (("default", 1.0), ("priority", fast)) if fast is not None else (("default", 1.0),)
    for tier, speed in tiers:
        for context in contexts:
            input_multiplier = speed * (2.0 if context else 1.0)
            output_multiplier = speed * (1.5 if context else 1.0)
            yield dict(base, service_tier=tier, threshold=context,
                       input=_scaled(base["input"], input_multiplier),
                       cache_read=_scaled(base["cache_read"], input_multiplier),
                       cache_write=_scaled(base["input"], input_multiplier), output=_scaled(base["output"], output_multiplier),
                       base_rates={key: base[key] for key in RATE_KEYS},
                       multipliers={"input": input_multiplier, "cache_read": input_multiplier,
                                    "cache_write": input_multiplier, "output": output_multiplier},
                       cache_write_basis="input", cache_write_surcharge=0.0,
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


def _price_signature(rows):
    # Fetch timestamps do not change the price version when the prices did not change.
    values = [{k: v for k, v in row.items() if k != "updated_at"} for row in rows]
    return hashlib.sha256(json.dumps([PRICING_RULE_VERSION, values], sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()[:16]


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
            self._seed = _read(Path(__file__).with_name("pricing_seed.json")).get("rows", [])
            self._cache = _read(self.cache_dir / "pricing_cache.json")
            self._overrides = _read(self.cache_dir / "pricing_overrides.json")
            self._rebuild()
            self.status = {"status": self._cache.get("status", "bundled"),
                           "updated_at": self._cache.get("updated_at", "2026-09-07T00:00:00+00:00"),
                           "error": self._cache.get("error"), "changed": False,
                           "price_version": self.price_version}
            try:
                self._archive_version()
            except OSError as exc:
                self.status["warning"] = "无法保存价格版本: " + str(exc)[:160]
            return self

    def _rebuild(self):
        merged = {}
        cached_rows = self._cache.get("rows", [])
        for raw in (self._seed if isinstance(self._seed, list) else []) + (cached_rows if isinstance(cached_rows, list) else []):
            row = _validate_row(raw)
            if row and _is_standard_base(row):
                row["service_tier"] = "default"
                key = _row_key(row)
                # A primary feed omitting an optional rate must not erase a verified seed rate.
                prior = merged.get(key, {})
                for rate in RATE_KEYS:
                    if row[rate] is None and prior.get(rate) is not None:
                        row[rate] = prior[rate]
                merged[key] = row
        self._base_rows = sorted((dict(row) for row in merged.values()), key=_row_key)
        for model, override_rows in self._overrides.items():
            if not isinstance(override_rows, list):
                continue
            for raw in override_rows:
                if not isinstance(raw, dict):
                    continue
                row = _validate_row(dict(raw, model=model, locked=True, source="manual"))
                if row and _is_standard_base(row):
                    row["service_tier"] = "default"
                    merged[_row_key(row)] = row
        self._standard_rows = sorted(merged.values(), key=_row_key)
        self._rows = sorted((row for base in self._standard_rows for row in _codex_rows(base)), key=_row_key)
        self.price_version = _price_signature(self._rows)

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
        model = model.strip()
        if not model:
            raise ValueError("模型名称不能为空")
        with self._lock:
            if rates is None:
                self._overrides.pop(model, None)
            else:
                raw = dict(rates, model=model, source="manual", locked=True, updated_at=_utcnow().isoformat())
                raw["service_tier"] = rates.get("service_tier", rates.get("tier", "default"))
                row = _validate_row(raw)
                if not row or any(rates.get(key) is not None and _number(rates[key]) is None for key in RATE_KEYS):
                    raise ValueError("输入与输出基础价必须填写，已填写单价必须为有限非负数")
                if not _is_standard_base(row):
                    raise ValueError("只编辑 Standard 基础价；Fast 与长上下文单价由 Codex 规则生成")
                row["service_tier"] = "default"
                old = self._overrides.get(model, [])
                self._overrides[model] = [r for r in old if not isinstance(r, dict) or not _is_standard_base(r)] + [row]
            _write(self.cache_dir / "pricing_overrides.json", self._overrides)
            self._rebuild()
            self._archive_version()
            self.status.update(price_version=self.price_version, changed=True)

    def price(self, record: dict):
        with self._lock:
            return self._price(record)

    def _price(self, record):
        result = {"usd": None, "pricing_status": "unpriced", "price_version": self.price_version,
                  "reason": "", "rates": {}, "pricing_basis": PRICING_BASIS}
        if "input_tokens" not in record or "output_tokens" not in record:
            return dict(result, pricing_status="invalid", reason="缺少输入或输出 Token 分项")
        counts = {}
        for key in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens"):
            value = _number(record.get(key, 0))
            if value is None or value != int(value):
                return dict(result, pricing_status="invalid", reason="Token 分项不是非负整数")
            counts[key] = int(value)
        total = counts["input_tokens"] + counts["output_tokens"]
        if ((counts["cached_input_tokens"] + counts["cache_write_input_tokens"] > counts["input_tokens"])
                or counts["reasoning_output_tokens"] > counts["output_tokens"]
                or ("total_tokens" in record and _number(record["total_tokens"]) != total)
                or str(record.get("quality", "")).lower().startswith("invalid")):
            return dict(result, pricing_status="invalid", reason="Token 分项不一致，未计算金额")
        if str(record.get("provider", "unknown")).lower() != "openai":
            return dict(result, reason="供应商未确认为 OpenAI")
        model = str(record.get("model", "")).removeprefix("openai/")
        model_rows = [r for r in self._rows if r["model"] == model]
        if not model_rows:
            return dict(result, reason="该模型尚无价格")
        raw_tier = record.get("service_tier")
        missing_tier = not raw_tier or raw_tier == "auto"
        aggregate = str(record.get("quality", "")).split(":", 1)[0] in (
            "aggregate", "cumulative", "cumulative_observation", "unresolved")
        estimated = missing_tier or aggregate
        tier = "default" if missing_tier or raw_tier == "standard" else str(raw_tier)
        if tier == "fast":
            tier = "priority"
        threshold = max((r["threshold"] for r in model_rows if counts["input_tokens"] > r["threshold"]), default=0)
        rates = next((r for r in model_rows if r["service_tier"] == tier and r["threshold"] == threshold), None)
        if rates is None:
            return dict(result, reason="缺少该模型与服务档位的 Codex 换算规则")
        # Cache creation remains ordinary input; Codex has no extra write charge.
        parts = {"input": counts["input_tokens"] - counts["cached_input_tokens"] - counts["cache_write_input_tokens"],
                 "cache_read": counts["cached_input_tokens"], "cache_write": counts["cache_write_input_tokens"],
                 "output": counts["output_tokens"]}
        if any(count and rates[key] is None for key, count in parts.items()):
            return dict(result, reason="缺少已使用 Token 类别的明确价格", rates=dict(rates))
        usd = math.fsum(count * (rates[key] or 0.0) / 1_000_000 for key, count in parts.items())
        multipliers = rates["multipliers"]
        reasons = [PRICING_BASIS_LABEL + "；输入/缓存读取 ×%g，输出 ×%g；缓存创建按普通输入价，无写入附加费" %
                   (multipliers["input"], multipliers["output"])]
        if missing_tier:
            reasons.append("服务档位缺失，按标准价估计")
        if aggregate:
            reasons.append("累计观测不能证明单次请求上下文与模型归属，仅供消费参考")
        return dict(result, usd=usd, pricing_status="estimated" if estimated else "priced", rates=dict(rates),
                    reason="；".join(reasons))

    def _fetch(self, url, etag=None):
        headers = {"Accept": "application/json", "User-Agent": "Codexio/2 pricing"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            with urlopen(Request(url, headers=headers), timeout=15) as response:
                # Bound an unexpected upstream response; feeds currently fit comfortably.
                payload = response.read(25_000_001)
                if len(payload) > 25_000_000:
                    raise ValueError("价格目录超过大小限制")
                return json.loads(payload.decode("utf-8")), response.headers.get("ETag"), False
        except HTTPError as exc:
            if exc.code == 304:
                return None, etag, True
            raise

    def sync(self, force=False):
        with self._lock:
            now = _utcnow()
            try:
                checked = datetime.fromisoformat(self._cache.get("checked_at", ""))
                if checked.tzinfo is None:
                    checked = checked.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                checked = None
            retry_after = 3600 if self._cache.get("status") == "offline" else 86400
            if not force and checked and 0 <= (now - checked).total_seconds() < retry_after:
                return dict(self.status, changed=False)
            previous = self.price_version
            previous_rows = [dict(row) for row in self._base_rows]
            errors = []
            etags = self._cache.get("etags", {})
            etags = dict(etags) if isinstance(etags, dict) else {}
            for url, parser in ((LITELLM_URL, _litellm_rows), (MODELS_DEV_URL, _models_dev_rows)):
                try:
                    active_source = self._cache.get("active_source")
                    data, etag, unchanged = self._fetch(url, etags.get(url) if active_source == url else None)
                    if unchanged:
                        if not any(row.get("source") == url for row in self._cache.get("rows", [])):
                            data, etag, unchanged = self._fetch(url)
                    if unchanged:
                        rows = [r for r in self._cache["rows"] if isinstance(r, dict) and _is_standard_base(r)]
                    else:
                        rows = parser(data, now.isoformat())
                        if not rows:
                            raise ValueError("价格目录中没有有效的 OpenAI 模型价格")
                    if etag:
                        etags[url] = etag
                    verification_rows = []
                    verification_status = "not_available"
                    warning = None
                    conflicts = set()
                    if url == LITELLM_URL:
                        try:
                            verification, verification_etag, verification_unchanged = self._fetch(MODELS_DEV_URL, etags.get(MODELS_DEV_URL))
                            if verification_unchanged:
                                verification_rows = self._cache.get("verification_rows", [])
                                if not verification_rows:
                                    verification, verification_etag, verification_unchanged = self._fetch(MODELS_DEV_URL)
                            if not verification_unchanged:
                                verification_rows = _models_dev_rows(verification, now.isoformat())
                            if not verification_rows:
                                raise ValueError("备源没有可核对的标准价格")
                            if verification_etag:
                                etags[MODELS_DEV_URL] = verification_etag
                            verification_status = "checked"
                            conflicts = _conflicting_models(rows, verification_rows)
                            if conflicts:
                                # Do not choose a conflicting source silently. Existing
                                # rows (including known service/context combinations)
                                # remain; new conflicting models stay unpriced.
                                rows = [r for r in rows if r["model"] not in conflicts]
                                rows.extend(r for r in previous_rows if r["model"] in conflicts and not r["locked"])
                                verification_status = "conflict"
                                # Cache now contains retained prices, not the rejected
                                # response. Fetch a full primary body on the next check.
                                etags.pop(LITELLM_URL, None)
                        except (OSError, ValueError, TypeError, KeyError) as exc:
                            warning = "备源核对暂不可用: " + str(exc)[:180]
                    self._cache = {"rows": rows, "updated_at": now.isoformat(), "checked_at": now.isoformat(),
                                   "etags": etags, "active_source": url,
                                   "verification_rows": verification_rows, "verification_status": verification_status,
                                   "conflicting_models": sorted(conflicts), "warning": warning,
                                   "status": "conflict" if conflicts else ("synced" if url == LITELLM_URL else "fallback"),
                                   "error": "两源单价冲突，保留已有价格: " + ", ".join(sorted(conflicts)) if conflicts else None}
                    self._rebuild()
                    self.status = {"status": self._cache["status"], "updated_at": now.isoformat(), "error": self._cache["error"],
                                   "verification_status": verification_status, "warning": warning,
                                   "conflicting_models": sorted(conflicts),
                                   "changed": self.price_version != previous, "price_version": self.price_version}
                    self._archive_version()
                    _write(self.cache_dir / "pricing_cache.json", self._cache)
                    return dict(self.status)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    errors.append(type(exc).__name__ + ": " + str(exc)[:200])
            self._cache.update(checked_at=now.isoformat(), error="; ".join(errors), status="offline")
            self.status.update(status="offline", error=self._cache["error"], changed=False)
            try:
                _write(self.cache_dir / "pricing_cache.json", self._cache)
            except OSError:
                pass
            return dict(self.status)
