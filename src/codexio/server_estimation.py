"""Same-account server Credits / weekly percentage, with explicit time bounds.

Daily rows are posted aggregates, never final invoices. Bounds assume constant
allowance within a segment; they include edge days and 1pp display uncertainty.
No hour-based allocation, overlapping-cost addition, or local-cost imputation.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from codexio.estimation import _finite, _time

DAY = timedelta(days=1)
WEEK = 604800
MATURITY = timedelta(hours=24)
MIN_DELTA = 10.0
PERCENT_ERROR = 1.0
BOUNDARY_SECONDS = 120
RESET_TOLERANCE = 60
USD_PER_CREDIT = 0.04


def midnight(stamp):
    return stamp.replace(hour=0, minute=0, second=0, microsecond=0)


def _segments(observations, account, now):
    rows = []
    for raw in observations:
        stamp, reset = _time(raw.get("timestamp")), _time(raw.get("reset_at"))
        pct = _finite(raw.get("used_percent"))
        if (raw.get("account_key") != account or raw.get("limit_id") != "codex"
                or raw.get("window_seconds") != WEEK or not stamp or not reset
                or not stamp < reset or (reset - stamp).total_seconds() > WEEK + RESET_TOLERANCE
                or stamp > now or pct is None or not 0 <= pct <= 100):
            continue
        early = _time(raw.get("sample_start")) or stamp
        if early > stamp or (stamp - early).total_seconds() > BOUNDARY_SECONDS:
            continue
        rows.append(dict(raw, _time=stamp, _early=early, _reset=reset, _pct=pct))
    rows.sort(key=lambda r: r["_time"])
    segments, segment = [], []
    for row in rows:
        # A 0% deadline of now+7d is an unstarted window, not a fresh reset.
        pending = row["_pct"] == 0 and abs((row["_reset"] - row["_time"]).total_seconds() - WEEK) <= 120
        changed = segment and (row.get("capacity_key") != segment[0].get("capacity_key")
                   or row.get("plan_type") != segment[0].get("plan_type")
                   or abs((row["_reset"] - segment[0]["_reset"]).total_seconds()) > RESET_TOLERANCE
                   or row["_pct"] < segment[-1]["_pct"])
        if pending or changed:
            if segment:
                segments.append((segment, True))
                segment = []
        if not pending:
            # Conflicting duplicates cannot be used as a stable boundary.
            if segment and row["_time"] == segment[-1]["_time"]:
                if row["_pct"] != segment[-1]["_pct"]:
                    segments.append((segment, True))
                    segment = []
                else:
                    continue
            segment.append(row)
    if segment:
        segments.append((segment, False))
    return segments


def _endpoints(segment):
    # Two observed endpoints per UTC day plus explicitly bracketed midnights.
    # At most ~30 endpoints in a weekly cycle, irrespective of polling rate.
    by_day = defaultdict(list)
    for row in segment:
        by_day[midnight(row["_time"])].append(row)
    selected = {r["_time"]: r for rows in by_day.values() for r in (rows[0], rows[-1])}
    boundary = midnight(segment[0]["_time"]) + DAY
    while boundary <= segment[-1]["_time"]:
        before = next((r for r in reversed(segment) if r["_time"] <= boundary
                       and (boundary - r["_time"]).total_seconds() <= BOUNDARY_SECONDS), None)
        after = next((r for r in segment if r["_early"] >= boundary
                      and (r["_time"] - boundary).total_seconds() <= BOUNDARY_SECONDS), None)
        if before and after and before["_pct"] == after["_pct"]:
            selected[boundary] = dict(before, _time=boundary, _early=boundary, _aligned=True)
        boundary += DAY
    for row in segment:
        if row["_time"] == midnight(row["_time"]) and row["_early"] == row["_time"]:
            selected[row["_time"]] = dict(row, _aligned=True)
    return sorted(selected.values(), key=lambda r: r["_time"])


def _candidate(a, b, ledger, now, rate):
    delta = b["_pct"] - a["_pct"]
    if delta < MIN_DELTA or b["_pct"] >= 100 or b["_early"] <= a["_time"]:
        return None, "insufficient"
    full, edges = [], []
    day = midnight(a["_early"])
    while day < b["_time"]:
        if day + DAY > a["_early"]:
            (full if a["_time"] <= day and day + DAY <= b["_early"] else edges).append(day)
        day += DAY
    if not full:
        return None, "short_interval"
    days = sorted(full + edges)
    if any(day + DAY + MATURITY > now for day in days):
        return None, "pending_daily"
    if any(day.date().isoformat() not in ledger for day in days):
        return None, "missing_daily"
    if any((_time(ledger[day.date().isoformat()].get("fetched_at")) or day) < day + DAY + MATURITY for day in days):
        return None, "pending_daily"
    if any(not ledger[day.date().isoformat()].get("scope_ok", False) for day in days):
        return None, "mixed_scope"
    cost = lambda seq: math.fsum(ledger[day.date().isoformat()]["credits"] for day in seq)
    try:
        low_credits, high_credits = cost(full), cost(days)
    except OverflowError:
        return None, "invalid_daily"
    if low_credits <= 0:
        return None, "insufficient"
    low, high = 100 * low_credits * rate / (delta + PERCENT_ERROR), 100 * high_credits * rate / (delta - PERCENT_ERROR)
    if not math.isfinite(low) or not math.isfinite(high):
        return None, "invalid_daily"
    aligned = not edges and a.get("_aligned") and b.get("_aligned")
    return dict(method="server_aligned" if aligned else "server_range", status="ready",
                start=a["_time"].isoformat(), end=b["_time"].isoformat(), delta_percent=delta,
                low_usd=low, high_usd=high, full_days=[d.date().isoformat() for d in full],
                edge_days=[d.date().isoformat() for d in edges], credits_low=low_credits, credits_high=high_credits,
                estimated_total_usd=100 * low_credits * rate / delta if aligned else None,
                daily_fetched_at=min(ledger[d.date().isoformat()].get("fetched_at", "") for d in days)), None


REASONS = {
    "collecting": "正在积累同一周期的服务端额度样本",
    "insufficient": "等待至少 10 个百分点的额度变化，且末端未达到 100%",
    "short_interval": "尚无包含完整 UTC 日的采样区间",
    "pending_daily": "等待边界日结束满 24 小时后使用已入账日数据",
    "missing_daily": "服务端缺少所需日数据，补齐后自动重算",
    "invalid_daily": "服务端计量数值异常，等待有效数据",
    "mixed_scope": "日数据涉及独立额度池或无法确认统计范围",
    "unknown_plan": "套餐未识别，暂不进行服务端估值",
    "inconsistent": "同周期数据得出的区间互不相容，等待补账或更多样本",
}


def estimate_server_weeks(observations, daily, account_key, *, now=None):
    now = _time(now) or datetime.now(timezone.utc)
    rate = USD_PER_CREDIT
    if not account_key:
        return []
    ledger, duplicates = {}, set()
    for row in daily:
        if row.get("account_key") != account_key:
            continue
        date = _time(row.get("date"))
        credits = _finite(row.get("credits"))
        if date and credits is not None and credits >= 0:
            key = date.date().isoformat()
            if key in ledger:
                duplicates.add(key)
            ledger[key] = dict(row, credits=credits)
    for key in duplicates:
        ledger.pop(key, None)
    output = []
    for segment, terminated in _segments(observations, account_key, now):
        first, last = segment[0], segment[-1]
        row = dict(account_key=account_key, plan_type=first.get("plan_type"), limit_id="codex",
                   reset_at=int(first["_reset"].timestamp()), start=first["_time"].isoformat(),
                   end=last["_time"].isoformat(), method="server_range", status="collecting",
                   is_closed=terminated or now >= first["_reset"], terminated=terminated,
                   estimated_total_usd=None, low_usd=None, high_usd=None, usd_per_credit=rate,
                   provisional=True, latest_percent=last["_pct"], delta_percent=last["_pct"]-first["_pct"])
        candidates, reasons = [], set()
        points = _endpoints(segment)
        for i, a in enumerate(points):
            for b in points[i+1:]:
                candidate, reason = _candidate(a, b, ledger, now, rate)
                if candidate:
                    candidates.append(candidate)
                elif reason:
                    reasons.add(reason)
        if not row["plan_type"] or row["plan_type"] in ("unknown", "legacy"):
            row["status"] = "unknown_plan"
        elif candidates:
            low = max(c["low_usd"] for c in candidates)
            high = min(c["high_usd"] for c in candidates)
            if low > high:
                row["status"] = "inconsistent"
            else:
                # Use the strongest actual interval. Other overlapping intervals
                # are consistency checks, not independent evidence to average.
                best = min(candidates, key=lambda c: (c["method"] != "server_aligned",
                           (c["high_usd"]-c["low_usd"])/c["low_usd"], -c["delta_percent"]))
                row.update(best, checked_intervals=len(candidates))
        elif reasons:
            row["status"] = next((s for s in ("invalid_daily", "mixed_scope", "missing_daily", "pending_daily", "short_interval", "insufficient") if s in reasons), "collecting")
        row["reason"] = REASONS.get(row["status"], "按同期服务端已入账 Credits 折算；日数据后续修正时自动重算")
        output.append(row)
    return sorted(output, key=lambda r: (r["end"], r["reset_at"]), reverse=True)
