"""Shared presentation rules for the subscription card and both history views."""
from datetime import datetime, timedelta, timezone

from codexio.estimation import _time
from codexio.money import usd
from codexio.server_usage import MESSAGES

ESTIMATE_HEADERS = ["套餐", "额度重置时间", "采样时间段", "整周估值（美元）", "状态", "计算依据", "额度池"]


def method_label(row):
    return {"server_aligned": "较高可信 · 日界对齐", "server_range": "全账号估算范围"}.get(row.get("method"), "本地观测参考")


def estimate_amount(row):
    if row.get("method") == "server_range":
        low, high = row.get("low_usd"), row.get("high_usd")
        return usd(low) + " – " + usd(high) if low is not None and high is not None else "—"
    value = row.get("estimated_total_usd")
    return usd(value) if value is not None else "—"


def estimate_detail(row):
    if not str(row.get("method", "")).startswith("server_"):
        return (row.get("reason") or "按本地已记录费用 / 同期额度变化推算") + "\n仅代表已配置日志来源；账号归属沿用本地历史设置。"
    parts = [row.get("reason") or "", "日数据按 UTC 分日（北京时间 08:00），结束满 24 小时后纳入；后续补账会修正。",
             "假设同期额度总量不变；范围包含边界日及 ±1 个百分点的差值误差。",
             "换算假设：1 Credit = $%g；非官方美元额度或订阅扣款。" % row.get("usd_per_credit", 0.04)]
    if row.get("low_usd") is not None:
        parts.append("估值范围：%s – %s" % (usd(row["low_usd"]), usd(row["high_usd"])))
    for field, label in (("full_days", "完整日"), ("edge_days", "边界日")):
        if row.get(field):
            parts.append(label + "：" + "、".join(row[field]))
    if row.get("daily_fetched_at"):
        stamp = _time(row["daily_fetched_at"])
        parts.append("所用日数据最早回查：" + (stamp.astimezone().strftime("%m/%d %H:%M") if stamp else row["daily_fetched_at"]))
    return "\n".join(parts)


def estimate_history(data):
    local = [r for r in data.get("weekly_estimates", [])
             if not (r.get("limit_id") == "codex_bengalfox" and not r.get("delta_percent") and not r.get("request_count"))]
    return list(data.get("weekly_server_estimates", [])) + local


def select_estimates(data, now=None):
    now = _time(now) or datetime.now(timezone.utc)
    context = data.get("server_usage_context") or {}
    status = context.get("quota_status")
    reset = _time(context.get("reset_at"))
    account = context.get("account_key")
    usable = account and status not in ("disabled", "auth_missing", "auth_expired", "account_mismatch", "no_weekly_quota")
    current = [r for r in data.get("weekly_server_estimates", []) if usable and not r.get("is_closed")
               and r.get("account_key") == account and reset and _time(r.get("reset_at"))
               and abs((_time(r["reset_at"]) - reset).total_seconds()) <= 60 and reset > now
               and r.get("plan_type") == context.get("plan_type")]
    current.sort(key=lambda r: r.get("end", ""), reverse=True)
    server = current[0] if current else None
    local = [r for r in data.get("weekly_estimates", []) if r.get("limit_id") == "codex"
             and r.get("account_key") not in (None, "", "unknown") and not r.get("is_closed") and not r.get("termination")
             and r.get("estimated_total_usd") is not None and (_time(r.get("reset_at")) or now) > now]
    if usable and reset:
        local = [r for r in local if abs(((_time(r.get("reset_at")) or now) - reset).total_seconds()) <= 60
                 and r.get("plan_type") == context.get("plan_type")]
    local.sort(key=lambda r: r.get("end", ""), reverse=True)
    reference = local[0] if local else None
    last_quota = _time(context.get("last_quota_at"))
    last_daily = _time(context.get("last_daily_at"))
    cached = bool(server and (status != "ok" or not last_quota or now - last_quota > timedelta(minutes=10)
                             or context.get("daily_status") != "ok" or not last_daily or now - last_daily > timedelta(hours=12)))
    message = MESSAGES.get(status, "")
    if status == "disabled":
        message = "服务端估算已关闭"
    elif status == "mock":
        message = "模拟数据"
    elif not message:
        message = (server or {}).get("reason") if not server or server.get("status") != "ready" else "服务端已入账数据，后续补账自动修正"
        message = message or "正在积累服务端多日数据"
    daily_status = context.get("daily_status")
    if daily_status in MESSAGES:
        message += "；日数据：" + MESSAGES[daily_status]
    primary = server if server and server.get("status") == "ready" else reference
    return dict(primary=primary or {}, reference=reference, server=server, cached=cached, message=message)
