"""Presentation rules for locally observed weekly quota estimates."""
from datetime import datetime, timezone

from codexio.estimation import _time
from codexio.money import usd

ESTIMATE_HEADERS = ["套餐", "额度重置时间", "采样时间段", "整周估值（美元）", "状态", "计算依据", "额度池"]


def method_label(row):
    return "本地观测估值"


def estimate_amount(row):
    value = row.get("estimated_total_usd")
    return usd(value) if value is not None else "—"


def estimate_detail(row):
    return (row.get("reason") or "按本地已记录费用 / 同期额度变化推算") + "\n仅代表已配置日志来源；账号归属沿用本地历史设置。"


def estimate_history(data):
    local = [r for r in data.get("weekly_estimates", [])
             if not (r.get("limit_id") == "codex_bengalfox" and not r.get("delta_percent") and not r.get("request_count"))]
    return local


def select_estimates(data, now=None):
    now = _time(now) or datetime.now(timezone.utc)
    local = [r for r in data.get("weekly_estimates", []) if r.get("limit_id") == "codex"
             and r.get("account_key") not in (None, "", "unknown") and not r.get("is_closed") and not r.get("termination")
             and r.get("estimated_total_usd") is not None and (_time(r.get("reset_at")) or now) > now]
    local.sort(key=lambda r: r.get("end", ""), reverse=True)
    primary = local[0] if local else None
    return dict(primary=primary or {}, reference=None, cached=False,
                message="仅按已配置来源的本地日志与同期周额度观测估算。" if primary else "正在积累本地调用与周额度样本。")
