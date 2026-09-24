"""Presentation rules for locally observed weekly quota estimates."""
from datetime import datetime, timezone

from codexio.money import usd

ESTIMATE_HEADERS = ["套餐", "采样时间段", "额度", "Token", "费用", "周估值"]


def _time(value):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def method_label(row):
    return "本地观测估值"


def estimate_amount(row):
    value = row.get("estimated_total_usd")
    return usd(value) if value is not None else "—"


def estimate_detail(row):
    return "按同一账号、套餐与额度周期内本机调用费用和额度变化估算；总 Token 仅用于展示。"


def estimate_history(data):
    return list(data.get("weekly_estimates", []))[:100]


def select_estimates(data, now=None):
    now = _time(now) or datetime.now(timezone.utc)
    identity = data.get("estimate_identity") or {}
    local = [r for r in data.get("weekly_estimates", []) if r.get("limit_id") == "codex"
             and r.get("estimated_total_usd") is not None
             and identity.get("account_key") and r.get("account_key") == identity.get("account_key")
             and r.get("plan_type") == identity.get("plan_type")
             and abs(float(r.get("reset_at") or 0) - float(identity.get("reset_at") or 0)) <= 60
             and (_time(r.get("reset_at")) or now) > now]
    local.sort(key=lambda r: r.get("end", ""), reverse=True)
    primary = local[0] if local else None
    return dict(primary=primary or {}, reference=None, cached=False,
                message="按本机已计价调用与同期周额度变化估算。" if primary else "等待同一账号与套餐下足够的额度变化和已计价调用。")
