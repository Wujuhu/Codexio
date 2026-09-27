"""Device-local activity; account profile endpoints never supply these metrics."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from codexio.confirmed_usage import confirmed_call, confirmed_tokens
from codexio.user_requests import normalized_tier


def _date(value):
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp).astimezone()
    except (TypeError, ValueError, OverflowError):
        return None


def _merged_seconds(intervals):
    intervals = sorted((start, end) for start, end in intervals if end >= start)
    if not intervals:
        return None
    start, end = intervals[0]
    result = 0.0
    for following, stop in intervals[1:]:
        if following <= end:
            end = max(end, stop)
        else:
            result += (end - start).total_seconds()
            start, end = following, stop
    return result + (end - start).total_seconds()


def local_activity(records, turns, now=None):
    now = (now or datetime.now().astimezone()).astimezone()
    daily, speeds, efforts = defaultdict(lambda: dict(tokens=0, known=0, calls=0)), Counter(), Counter()
    local = []
    for row in records:
        stamp = _date(row.get("timestamp"))
        if stamp is None or stamp > now:
            continue
        local.append(row)
        day = daily[stamp.date()]
        tokens = confirmed_tokens(row)
        if tokens is not None:
            day["tokens"] += tokens
            day["known"] += 1
        if confirmed_call(row):
            day["calls"] += 1
            tier = normalized_tier(row.get("service_tier"))
            speeds[tier if tier in ("priority", "default") else "unknown"] += 1
            efforts[str(row.get("reasoning_effort") or "unknown")] += 1
    active = sorted(day for day, value in daily.items() if value["calls"])
    active_set = set(active)
    longest = streak = 0
    previous = None
    for day in active:
        streak = streak + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, streak)
        previous = day
    cursor = now.date() if now.date() in active_set else now.date() - timedelta(days=1)
    current = 0
    while cursor in active_set:
        current += 1
        cursor -= timedelta(days=1)
    pairs = {(row.get("session_id"), row.get("turn_id")) for row in local}
    parents = {row.get("session_id"): row.get("parent_session_id") for row in turns
               if row.get("is_subagent") and row.get("parent_session_id")}

    def root(session):
        seen = set()
        while session in parents and session not in seen:
            seen.add(session)
            session = parents[session]
        return session

    intervals, partial = defaultdict(list), False
    for row in turns:
        if (row.get("session_id"), row.get("turn_id")) not in pairs:
            continue
        start, end = _date(row.get("started_at")), _date(row.get("ended_at"))
        duration = row.get("duration_ms")
        if end and (not start or row.get("started_inferred")) and isinstance(duration, (int, float)) and duration >= 0:
            start = end - timedelta(milliseconds=duration)
        reliable = not row.get("started_inferred", True) or isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0
        if reliable and row.get("status") in ("completed", "aborted") and start and end and now >= end >= start:
            intervals[root(row.get("session_id"))].append((start, end))
        else:
            partial = True
    durations = [value for value in (_merged_seconds(items) for items in intervals.values()) if value is not None]
    calls = sum(speeds.values())
    known_efforts = [(key, value) for key, value in efforts.items() if key != "unknown"]
    most = max(known_efforts, key=lambda value: value[1]) if known_efforts else (None, 0)
    values = [value["tokens"] for value in daily.values() if value["known"]]
    buckets = []
    for offset in range(364, -1, -1):
        day = now.date() - timedelta(days=offset)
        value = daily.get(day, {})
        stamp = datetime.combine(day, datetime.min.time()).astimezone()
        buckets.append(dict(timestamp=stamp.isoformat(), tokens=value.get("tokens", 0), requests=value.get("calls", 0)))
    return dict(total_tokens=sum(values) if values else None, peak_daily_tokens=max(values) if values else None,
                longest_chat_seconds=max(durations) if durations else None, duration_partial=partial,
                current_streak_days=current, longest_streak_days=longest,
                fast_percent=speeds["priority"] / calls * 100 if calls else None,
                most_used_effort=most[0], effort_percent=most[1] / calls * 100 if calls and most[0] else None,
                unknown_speed=speeds["unknown"], unknown_effort=efforts["unknown"], calls=calls,
                daily=buckets, scanned_at=now.isoformat())


def local_threads(records, turns):
    titles, totals, created, parents = {}, Counter(), {}, {}
    for row in turns:
        session = row.get("session_id")
        if not session:
            continue
        if row.get("is_subagent") and row.get("parent_session_id"):
            parents[session] = row["parent_session_id"]
        start = _date(row.get("started_at"))
        if start:
            created[session] = min(created.get(session, start), start)
    for row in records:
        session = row.get("session_id")
        if not session:
            continue
        totals[session] += confirmed_tokens(row) or 0
        titles[session] = row.get("session_title") or row.get("prompt_preview") or session
    def root(session):
        seen = set()
        while session in parents and session not in seen:
            seen.add(session)
            session = parents[session]
        return session
    children = defaultdict(set)
    for session in set(totals) | set(parents):
        owner = root(session)
        if owner != session:
            children[owner].add(session)
    roots = {root(session) for session in totals}
    return [dict(thread_id=session, title=titles.get(session) or session,
                 created_at=created[session].isoformat() if session in created else None,
                 descendant_thread_ids=sorted(children[session]),
                 local_tokens=sum(totals[key] for key in {session, *children[session]}))
            for session in sorted(roots, key=lambda key: totals[key], reverse=True)]
