"""Read-only Codex account analytics. Credentials stay in memory, never in SQLite.

These ChatGPT endpoints have no public stability/completeness contract. Schema
or authentication failures keep the existing local estimate available.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from codexio import __version__
from codexio.confirmed_usage import confirmed_count
from codexio.estimation import _finite, _time

BASE = "https://chatgpt.com/backend-api/wham/"
MAX_BYTES = 8 * 1024 * 1024
MESSAGES = {
    "auth_missing": "未找到可用的 Codex 登录，请在 Codex 中登录后重试",
    "auth_expired": "Codex 登录已失效，等待 Codex 更新登录状态",
    "account_mismatch": "服务端账号与当前登录不一致，已暂停服务端估值",
    "rate_limited": "服务端限制请求，稍后自动重试",
    "unavailable": "服务端统计暂不可用，稍后自动重试",
    "network_error": "服务端连接失败，保留已缓存数据",
    "invalid_data": "服务端统计格式发生变化，等待有效数据",
    "no_weekly_quota": "当前账号未返回可识别的 Codex 周额度",
    "storage_error": "服务端统计缓存暂不可用",
}


class ServerUsageError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES.get(code, MESSAGES["unavailable"]))


@dataclass(frozen=True)
class Credentials:
    token: str = field(repr=False)
    account_id: str = field(repr=False)
    subject: str = field(repr=False)

    @property
    def key(self):
        # Account ID can identify a shared workspace. Include the signed-in
        # user so two members of the same workspace never share a quota ledger.
        return hashlib.sha256(json.dumps(["codexio-account-v1", self.account_id, self.subject]).encode()).hexdigest()


def read_credentials(root=None):
    root = Path(root or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((root / "auth.json").read_text(encoding="utf-8"))
        tokens = data.get("tokens") if isinstance(data, dict) else None
        token, account = (tokens.get(k) for k in ("access_token", "account_id")) if isinstance(tokens, dict) else (None, None)
        if not all(isinstance(v, str) and v.strip() and not any(c in v for c in "\r\n") for v in (token, account)):
            raise ValueError()
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError()
        # Claims only namespace the cache. The authenticated response must
        # still match the requested account before any samples are accepted.
        return Credentials(token, account, subject)
    except (OSError, ValueError, UnicodeError, IndexError, AttributeError):
        raise ServerUsageError("auth_missing") from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer or account header to a redirected endpoint.
        return None


def parse_quota(data, credentials, *, started, finished):
    if not isinstance(data, dict):
        raise ServerUsageError("invalid_data")
    if data.get("account_id") != credentials.account_id:
        raise ServerUsageError("account_mismatch")
    limits = data.get("rate_limit")
    if not isinstance(limits, dict):
        raise ServerUsageError("no_weekly_quota")
    matches = [w for k in ("primary_window", "secondary_window") if isinstance(w := limits.get(k), dict)
               and w.get("limit_window_seconds") == 604800]
    if len(matches) != 1:
        raise ServerUsageError("no_weekly_quota")
    window = matches[0]
    pct, reset = _finite(window.get("used_percent")), _time(window.get("reset_at"))
    if pct is None or not 0 <= pct <= 100 or reset is None or reset <= finished or (reset-finished).total_seconds() > 604860:
        raise ServerUsageError("invalid_data")
    plan = data.get("plan_type")
    plan = plan.lower() if isinstance(plan, str) and len(plan) < 80 else None
    signature = {k: data.get(k) for k in ("plan_type", "plan_id", "rate_limit_tier")}
    return dict(account_key=credentials.key, timestamp=finished.isoformat(), sample_start=started.isoformat(),
                plan_type=plan, capacity_key=hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest(),
                limit_id="codex", window_seconds=604800, used_percent=pct, reset_at=int(reset.timestamp()))


def parse_daily(data, credentials, start, end, fetched_at):
    if not isinstance(data, dict) or data.get("balance_unit") != "credit" or data.get("group_by") != "day":
        raise ServerUsageError("invalid_data")
    if "account_id" in data and data["account_id"] != credentials.account_id:
        raise ServerUsageError("account_mismatch")
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ServerUsageError("invalid_data")
    output, seen = [], set()
    for raw in rows:
        try:
            day = date.fromisoformat(raw["date"])
            totals = raw["totals"]
            if not isinstance(totals, dict) or not start <= day <= end or day in seen:
                raise ValueError()
            credits = _finite(totals.get("credits"))
            if credits is None or credits < 0:
                raise ValueError()
            counts = {k: confirmed_count(totals.get(k)) for k in (
                "uncached_text_input_tokens", "cached_text_input_tokens", "text_output_tokens", "text_total_tokens")}
            if any(v is None for v in counts.values()) or sum(list(counts.values())[:3]) != counts["text_total_tokens"]:
                raise ValueError()
            models = raw.get("models")
            # Model Credits can be zero even when daily totals are positive.
            # Any reported Spark activity makes the whole day unsafe to pair
            # with the normal Codex pool; do not subtract an invented amount.
            scope_ok = isinstance(models, list) and (bool(models) or credits == 0 and counts["text_total_tokens"] == 0)
            for model in models if isinstance(models, list) else []:
                name = str(model.get("model") or "").lower() if isinstance(model, dict) else ""
                if not name or "spark" in name or "bengalfox" in name:
                    scope_ok = False
            seen.add(day)
            output.append(dict(account_key=credentials.key, date=day.isoformat(), credits=credits,
                               scope_ok=scope_ok, fetched_at=fetched_at.isoformat(), **counts))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ServerUsageError("invalid_data") from None
    return output


class ServerUsageClient:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(_NoRedirect())

    def _get(self, path, credentials):
        request = Request(BASE + path, headers={"Authorization": "Bearer " + credentials.token,
            "ChatGPT-Account-Id": credentials.account_id, "Accept": "application/json",
            "User-Agent": "Codexio/" + __version__, "Cache-Control": "no-cache"})
        try:
            with self.opener.open(request, timeout=12) as response:
                if response.geturl() != request.full_url:
                    raise ServerUsageError("unavailable")
                payload = response.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ServerUsageError("invalid_data")
            return json.loads(payload.decode("utf-8"))
        except HTTPError as exc:
            code = "auth_expired" if exc.code in (401, 403) else "rate_limited" if exc.code == 429 else "unavailable"
            raise ServerUsageError(code) from None
        except (URLError, TimeoutError, OSError):
            raise ServerUsageError("network_error") from None
        except (ValueError, UnicodeError):
            raise ServerUsageError("invalid_data") from None

    def quota(self, credentials):
        started = datetime.now(timezone.utc)
        data = self._get("usage", credentials)
        return parse_quota(data, credentials, started=started, finished=datetime.now(timezone.utc))

    def daily(self, credentials, start, end):
        query = urlencode(dict(start_date=start.isoformat(), end_date=end.isoformat(), group_by="day", workspace_user="true"))
        data = self._get("analytics/daily-workspace-usage-counts?" + query, credentials)
        return parse_daily(data, credentials, start, end, datetime.now(timezone.utc))
