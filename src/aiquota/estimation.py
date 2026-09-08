"""Observed subscription quota value, not an OpenAI dollar allowance.

Sub2API displays same-window logged dollars beside upstream quota percentages:
https://github.com/Wei-Shaw/sub2api/blob/909b8c7ef44cfc7eeae18e37f629c4e58c9b727b/backend/internal/service/account_usage_service.go#L711-L771
The delta-cost / delta-percent projection below is this application's estimate.
It cannot prove absence of usage on an unconfigured device or in the cloud.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

WEEK_SECONDS = 7 * 24 * 3600
MIN_PERCENT_SPAN = 5.0
MATURITY_SECONDS = 120
RESET_TOLERANCE_SECONDS = 60
_UNKNOWN = (None, "", "unknown", "legacy", "legacy_unknown")


def _time(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (float, int)) and not isinstance(value, bool):
        try:
            parsed = datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _bucket(item):
    value = item.get("limit_id")
    if value not in _UNKNOWN:
        return str(value)
    # Only the collector may map a provably single-bucket legacy record. Model
    # names and absent metadata cannot establish an account-wide quota scope.
    return "unknown"


def _account(item, stamp, since, assignments):
    for assignment in assignments:
        if assignment.get("source_id") != item.get("source_id"):
            continue
        start, end = _time(assignment.get("start")), _time(assignment.get("end"))
        key = assignment.get("account_key")
        if key not in _UNKNOWN and start and end and start <= stamp < end:
            return str(key)
    if since and stamp >= since:
        key = item.get("account_key")
        return str(key) if key not in _UNKNOWN else "current"
    return None


def _drop_segments(observations):
    """Reject transient stale samples; split confirmed >1pp regressions."""
    result, segment = [], []
    high = -1.0
    index = 0
    while index < len(observations):
        current = observations[index]
        value = current["_percent"]
        if segment and value < high:
            if high - value <= 1.0:
                index += 1
                continue
            later = next((o for o in observations[index + 1:]
                          if (o["_time"] - current["_time"]).total_seconds() >= 60), None)
            if later is None or later["_percent"] >= high - 1.0:
                index += 1
                continue
            result.append((segment, "quota_regression"))
            segment, high = [], -1.0
        if segment and current["_time"] == segment[-1]["_time"]:
            # At the same instant choose the lower endpoint value conservatively.
            if value < segment[-1]["_percent"]:
                segment[-1] = current
            index += 1
            continue
        segment.append(current)
        high = max(high, value)
        index += 1
    if segment:
        result.append((segment, None))
    return result


def _valid_cost(record):
    value = _finite(record.get("cost_usd"))
    return value is not None and value >= 0 and record.get("pricing_status") in ("priced", "estimated")


def _calibratable(record):
    return _valid_cost(record) and record.get("pricing_status") == "priced"


def _tokens(record):
    value = _finite(record.get("total_tokens"))
    return max(0, int(value)) if value is not None else 0


def estimate_weeks(records: list[dict], observations: list[dict], account_since,
                   now=None, sources_complete=True, assignments=None) -> list[dict]:
    """Return newest observed periods first; re-running safely revises late history.

    start/end and consumed_* refer to the same (start, end] calibration interval.
    Pre-enrolment history requires explicit per-source/time account assignments.
    `sources_complete` means all configured sources have finished indexing, not
    that the application has visibility into every account-wide consumption path.
    """
    current = _time(now) or datetime.now(timezone.utc)
    since = _time(account_since)
    assignments = [a for a in (assignments or []) if isinstance(a, dict)]
    ledger = []
    seen = set()
    for record in records:
        stamp = _time(record.get("timestamp"))
        if not stamp or stamp > current or str(record.get("provider", "openai")).lower() != "openai":
            continue
        identity = record.get("id") or record.get("response_id")
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        ledger.append(dict(cost_usd=record.get("cost_usd"), pricing_status=record.get("pricing_status"),
                           total_tokens=record.get("total_tokens"), _time=stamp, _bucket=_bucket(record),
                           _account=_account(record, stamp, since, assignments)))
    ledger.sort(key=lambda r: r["_time"])

    prepared, observation_ids = [], set()
    for observation in observations:
        stamp = _time(observation.get("timestamp"))
        reset = _time(observation.get("resets_at"))
        percent = _finite(observation.get("used_percent"))
        minutes = _finite(observation.get("window_minutes"))
        if not stamp or not reset or stamp > current or percent is None or not 0 <= percent <= 100:
            continue
        if minutes is None or abs(minutes - 10080) > 10:
            continue
        item = dict(plan_type=observation.get("plan_type"), pairing_delay_seconds=observation.get("pairing_delay_seconds", 0),
                    _time=stamp, _reset=reset, _percent=percent,
                    _account=_account(observation, stamp, since, assignments), _bucket=_bucket(observation))
        identity = (stamp, reset, percent, observation.get("plan_type"), item["_account"], item["_bucket"])
        if identity not in observation_ids:
            prepared.append(item)
            observation_ids.add(identity)
    prepared.sort(key=lambda o: o["_time"])

    groups, active = [], {}
    for item in prepared:
        key = item["_account"], item["_bucket"]
        group = active.get(key)
        changed_plan = group and group["plan_type"] != item.get("plan_type")
        changed_reset = group and abs((group["reset"] - item["_reset"]).total_seconds()) > RESET_TOLERANCE_SECONDS
        if group is None or changed_plan or changed_reset:
            if group:
                group["next_at"] = item["_time"]
                if changed_plan:
                    group["termination"] = "plan_changed"
                elif item["_time"] < group["reset"] - timedelta(seconds=RESET_TOLERANCE_SECONDS):
                    group["termination"] = "early_reset"
            group = {"account": key[0], "bucket": key[1], "plan_type": item.get("plan_type"),
                     "reset": item["_reset"], "observations": [], "termination": None}
            groups.append(group)
            active[key] = group
        group["observations"].append(item)

    output = []
    mature_before = current - timedelta(seconds=MATURITY_SECONDS)
    for group in groups:
        for segment, termination in _drop_segments(group["observations"]):
            termination = termination or group["termination"]
            # A log carrying a very old request's counters is not a reliable endpoint.
            eligible = [o for o in segment if o["_time"] <= mature_before
                        and o["_time"] < group["reset"] and o["_percent"] < 100
                        and (_finite(o.get("pairing_delay_seconds", 0)) or 0) <= MATURITY_SECONDS]
            display = eligible if eligible else segment
            start, end = display[0]["_time"], display[-1]["_time"]
            matching = [r for r in ledger if r["_bucket"] == group["bucket"] and r["_account"] == group["account"]]

            # Resume after the last unpriced/invalid request instead of extrapolating
            # a partial dollar numerator against a fully-accounted percentage.
            if len(eligible) >= 2:
                bad = [r for r in matching if start < r["_time"] <= end and not _calibratable(r)]
                if bad:
                    after_gap = [o for o in eligible if o["_time"] >= bad[-1]["_time"]]
                    if len(after_gap) >= 2 and after_gap[-1]["_percent"] - after_gap[0]["_percent"] >= MIN_PERCENT_SPAN:
                        eligible = after_gap
                        start, end = eligible[0]["_time"], eligible[-1]["_time"]
            interval = [r for r in matching if start < r["_time"] <= end]
            dollars = math.fsum(float(r["cost_usd"]) for r in interval if _valid_cost(r))
            missing = sum(not _valid_cost(r) for r in interval)
            estimated_prices = sum(r.get("pricing_status") == "estimated" for r in interval)
            unknown_scope = sum(r["_bucket"] == "unknown" and r["_account"] == group["account"]
                                and start < r["_time"] <= end for r in ledger)
            delta = eligible[-1]["_percent"] - eligible[0]["_percent"] if len(eligible) >= 2 else 0.0
            row = {"reset_at": int(group["reset"].timestamp()), "plan_type": group["plan_type"],
                   "limit_id": group["bucket"], "account_key": group["account"],
                   "start": start.isoformat(), "end": end.isoformat(), "consumed_usd": dollars,
                   "consumed_tokens": sum(_tokens(r) for r in interval), "delta_percent": delta,
                   "estimated_total_usd": None, "estimated_remaining_usd": None,
                   "rounding_low_usd": None, "rounding_high_usd": None,
                   "status": "insufficient", "reason": "等待至少 5 个百分点的同期观测",
                   "is_closed": current >= group["reset"], "coverage": "observed_interval",
                   "missing_price_requests": missing, "estimated_price_requests": estimated_prices,
                   "unknown_scope_requests": unknown_scope,
                   "request_count": len(interval), "termination": termination}
            if group["account"] is None:
                row.update(status="unattributed", reason="历史尚未归属当前账号；仅展示已记录消费")
            elif group["plan_type"] in _UNKNOWN:
                row.update(status="unknown_plan", reason="订阅类型缺失，不能与其他套餐区间合并校准")
            elif group["bucket"] == "unknown" or unknown_scope:
                row.update(status="unknown_limit", reason="存在额度桶未确认的请求，不能将其归入默认 Codex 额度校准")
            elif not sources_complete:
                row.update(status="incomplete", reason="日志来源尚未完整同步，等待补齐后重新估算")
            elif missing:
                row.update(status="unpriced", reason="观测区间包含未定价或无效计量，不能用部分成本推算全部额度")
            elif estimated_prices:
                row.update(status="estimated_prices", reason="服务档位等计价条件不足；标准价估计可展示消费，但不能用于严格额度校准")
            elif len(eligible) < 2:
                row.update(status="collecting", reason="等待两次相隔足够用量且已成熟的额度观测")
            elif delta < MIN_PERCENT_SPAN:
                pass
            elif dollars <= 0:
                row.update(status="incomplete", reason="额度发生变化，但没有匹配的本地已定价用量")
            else:
                estimate = 100.0 * dollars / delta
                latest = segment[-1]["_percent"]
                row.update(status="ready", estimated_total_usd=estimate,
                           estimated_remaining_usd=max(0.0, estimate * (100.0 - latest) / 100.0),
                           reason="按同期 API 等价成本 / 额度变化推算；仅代表已配置来源及该模型组合")
                if all(o["_percent"].is_integer() for o in (eligible[0], eligible[-1])):
                    row.update(rounding_low_usd=100.0 * dollars / (delta + 1.0),
                               rounding_high_usd=100.0 * dollars / (delta - 1.0))
                # No fabricated complete-week claim: normal closure, explicit zero
                # baseline near the start, a late endpoint, and no trailing requests.
                cycle_start = group["reset"] - timedelta(seconds=WEEK_SECONDS)
                last_tokens = [r for r in matching if end < r["_time"] < group["reset"]]
                if (row["is_closed"] and not termination and eligible[0]["_percent"] == 0
                        and cycle_start <= start <= cycle_start + timedelta(hours=24)
                        and end >= group["reset"] - timedelta(hours=6) and not last_tokens
                        and sources_complete and not missing):
                    row["coverage"] = "closed_cycle_observed"
            output.append(row)
    return sorted(output, key=lambda row: (row["reset_at"], row["end"]), reverse=True)
