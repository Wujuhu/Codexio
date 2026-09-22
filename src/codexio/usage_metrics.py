"""Cache ratios and elapsed-time output rates, with explicit missing data."""
from __future__ import annotations

import math

from codexio.confirmed_usage import ConfirmedUsage, confirmed_count
from codexio.durations import elapsed_milliseconds


def cache_counts(record):
    inp = confirmed_count(record.get("input_tokens"))
    cached = confirmed_count(record.get("cached_input_tokens"))
    if inp is None or cached is None or cached > inp or str(record.get("quality", "")).startswith("invalid"):
        return None
    return inp, cached


class CacheUsage:
    def __init__(self):
        self.input = self.cached = self.valid = self.skipped = 0

    def add(self, record):
        counts = cache_counts(record)
        if counts is None:
            self.skipped += 1
        else:
            self.input += counts[0]
            self.cached += counts[1]
            self.valid += 1

    def summary(self):
        return dict(cache_hit_rate=self.cached / self.input if self.input else None,
                    cache_input_tokens=self.input, cache_read_tokens=self.cached,
                    cache_valid_records=self.valid, cache_skipped_records=self.skipped)


def cache_hit_rate(record):
    if "cache_hit_rate" in record:
        value = record["cache_hit_rate"]
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1 else None
    counts = cache_counts(record)
    return counts[1] / counts[0] if counts and counts[0] else None


def cache_percentage(value):
    return "—" if value is None else "%.1f%%" % (value * 100)


def cache_tooltip(record):
    note = "缓存命中率 = 缓存读取 Token ÷ 输入 Token（含缓存）；不把缓存创建计为命中。"
    if record.get("cache_skipped_records"):
        note += "\n按缓存计量完整的调用加权计算，缺失分项未按零处理。"
    return note


def output_speed(record, now=None):
    tokens = confirmed_count(record.get("output_tokens"))
    elapsed = elapsed_milliseconds(record, now)
    if (tokens is None or elapsed is None or elapsed <= 0
            or str(record.get("quality", "")).startswith("invalid")
            or record.get("record_kind") == "user_request" and not record.get("call_count")):
        return None
    try:
        value = tokens / (elapsed / 1000)
    except (OverflowError, ZeroDivisionError):
        return None
    return value if math.isfinite(value) else None


def output_speed_text(record, now=None):
    value = output_speed(record, now)
    if value is None:
        return "—"
    return "%d Token/s" % math.floor(value + .5)


def output_speed_tooltip(record):
    detail = ("整轮耗时包含工具等待和其他停顿，表示整轮平均输出速度。" if record.get("record_kind") == "user_request"
              else "使用日志明确记录的本次调用耗时，不用相邻调用间隔推算。")
    return "平均输出速度 = 输出 Token ÷ 本行耗时。\n" + detail + "\n缺少有效 Token 或可靠计时则显示 —。"


def dashboard_summary(records, user_requests):
    totals, cache = ConfirmedUsage(), CacheUsage()
    for record in records:
        totals.add(record)
        cache.add(record)
    summary = totals.summary()
    summary.update(cache.summary(), user_requests=user_requests)
    summary["valid_counts"].update(user_requests=user_requests, cache_hit_rate=cache.valid)
    summary["skipped"].update(user_requests=0, cache_hit_rate=cache.skipped)
    return summary


def dashboard_comparison(current, previous, period, bounds):
    changes = {}
    for key in ("tokens", "usd", "requests", "user_requests", "cache_hit_rate"):
        value, baseline = current[key], previous[key]
        status, percent = "ready", None
        if value is None:
            status = "no_current"
        elif baseline is None:
            status = "no_history"
        elif baseline == 0 and value != 0:
            status = "zero_baseline"
        else:
            percent = (value - baseline) / baseline * 100 if baseline else 0.0
        changes[key] = dict(status=status, percent=percent)
    start, end, previous_start, previous_end = bounds
    return dict(period=period, current=current, previous=previous, changes=changes,
                label={"today": "较昨天", "week": "较前 7 天", "month": "较前 30 天"}[period],
                start=start.isoformat(), end=end.isoformat(), previous_start=previous_start.isoformat(),
                previous_end=previous_end.isoformat())
