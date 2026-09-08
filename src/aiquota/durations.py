"""Source-grounded elapsed times; concurrent work is never summed twice."""
from __future__ import annotations

import math
from datetime import datetime, timezone


def valid_milliseconds(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _time(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def request_duration_fields(meta, members, status, pending=False):
    result = dict(duration_ms=None, duration_running=False, duration_started_at=None)
    start = _time(meta.get("started_at")) if not meta.get("started_inferred", True) else None
    if status == "running":
        if start is not None:
            result.update(duration_running=True, duration_started_at=start.isoformat())
        return result
    if status not in ("completed", "aborted") or pending:
        return result
    reported = valid_milliseconds(meta.get("duration_ms"))
    ends = [_time(member.get("ended_at")) for member in members]
    if any(value is None for value in ends) or not ends:
        return result
    own_end = _time(meta.get("ended_at"))
    final_end = max(ends)
    if reported is not None and own_end is not None and final_end <= own_end:
        result["duration_ms"] = reported
    elif start is not None and final_end >= start:
        result["duration_ms"] = max(reported or 0, (final_end - start).total_seconds() * 1000)
    return result


def elapsed_milliseconds(record, now=None):
    if record.get("duration_running"):
        start = _time(record.get("duration_started_at"))
        end = _time(now) if now is not None else datetime.now(timezone.utc)
        if start is not None and end is not None and end >= start:
            return (end - start).total_seconds() * 1000
        return None
    return valid_milliseconds(record.get("duration_ms"))


def duration_text(record, now=None):
    value = elapsed_milliseconds(record, now)
    if value is None:
        return "—"
    if value < 1000:
        return "%.2f 秒" % (value / 1000)
    seconds = int(value / 1000)
    if seconds < 60:
        return "%.1f 秒" % (value / 1000)
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return "%d分%02d秒" % (minutes, seconds)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "%d时%02d分%02d秒" % (hours, minutes, seconds)
    days, hours = divmod(hours, 24)
    return "%d天%02d时%02d分" % (days, hours, minutes)


def duration_tooltip(record):
    if record.get("duration_running"):
        return "进行中：从主请求发起时间累计，包含工具等待；并行子代理不重复相加。"
    value = elapsed_milliseconds(record)
    if value is None:
        return ("缺少完整的请求起止时间，无法确认总耗时。" if record.get("record_kind") == "user_request" else
                "日志未记录这次模型调用的独立耗时。")
    detail = "总耗时 %.3f 秒。" % (value / 1000)
    if record.get("record_kind") == "user_request":
        return detail + "从主请求发起到整轮结束，包含工具等待；并行子代理不重复相加。"
    return detail + "使用日志明确记录的调用计时，不以相邻调用间隔代替。"
