from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codexio.rate_limits import (
    QuotaState,
    QuotaStatus,
    WindowView,
    classify_windows,
    format_reset_time,
    merge_rate_limit_snapshots,
    next_backoff_seconds,
    parse_rate_limits_result,
    quota_state_from_snapshot,
    remaining_percent,
    should_show_five_hour,
    snapshot_from_cache,
    snapshot_to_cache,
    stale_after_seconds,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_classifies_windows_by_duration_not_slot() -> None:
    snapshot = parse_rate_limits_result(
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 50,
                    "windowDurationMins": 10080,
                    "resetsAt": 1731542400,
                },
                "secondary": {
                    "usedPercent": 20,
                    "windowDurationMins": 300,
                    "resetsAt": 1730947200,
                },
            }
        }
    )
    state = quota_state_from_snapshot(snapshot, status=QuotaStatus.OK, message="ok")
    assert state.five_hour.remaining_percent == 80
    assert state.week.remaining_percent == 50


def test_maps_five_hour_and_week_windows() -> None:
    snapshot = parse_rate_limits_result(_load("rate_limits_read.json")["result"])
    five_hour, week = classify_windows(snapshot)
    assert five_hour is not None
    assert week is not None
    assert five_hour.window_duration_mins == 300
    assert week.window_duration_mins == 10080
    state = quota_state_from_snapshot(snapshot, status=QuotaStatus.OK, message="ok")
    assert state.five_hour.remaining_percent == 72
    assert state.week.remaining_percent == 39
    assert state.five_hour.resets_at is not None
    assert state.week.resets_at is not None


def test_single_week_window_is_classified_as_week() -> None:
    snapshot = parse_rate_limits_result(
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 25,
                    "windowDurationMins": 10080,
                    "resetsAt": 1731542400,
                }
            }
        }
    )
    five_hour, week = classify_windows(snapshot)
    assert five_hour is None
    assert week is not None
    assert week.used_percent == 25
    state = quota_state_from_snapshot(snapshot, status=QuotaStatus.OK, message="ok")
    assert state.five_hour.remaining_percent is None
    assert state.week.remaining_percent == 75


def test_single_unmatched_window_is_treated_as_week() -> None:
    snapshot = parse_rate_limits_result(
        {
            "rateLimits": {
                "primary": {
                    "usedPercent": 40,
                    "resetsAt": 1731542400,
                }
            }
        }
    )
    five_hour, week = classify_windows(snapshot)
    assert five_hour is None
    assert week is not None
    assert week.used_percent == 40


def test_should_show_five_hour_auto_hides_when_only_week_exists() -> None:
    week = WindowView(70, 30, 10080, None)
    only_week = QuotaState(
        five_hour=WindowView.unavailable(),
        week=week,
        status=QuotaStatus.OK,
        message="ok",
    )
    both = QuotaState(
        five_hour=WindowView(90, 10, 300, None),
        week=week,
        status=QuotaStatus.OK,
        message="ok",
    )
    empty = QuotaState.empty()
    assert should_show_five_hour("week", both) is False
    assert should_show_five_hour("both", only_week) is True
    assert should_show_five_hour("auto", only_week) is False
    assert should_show_five_hour("auto", both) is True
    assert should_show_five_hour("auto", empty) is True


def test_missing_known_windows_are_na() -> None:
    snapshot = parse_rate_limits_result(_load("rate_limits_missing_windows.json")["result"])
    state = quota_state_from_snapshot(snapshot, status=QuotaStatus.OK, message="ok")
    assert state.five_hour.remaining_percent is None
    assert state.week.remaining_percent is None


def test_accepts_snake_case_and_float_used_percent() -> None:
    snapshot = parse_rate_limits_result(_load("rate_limits_snake_case.json")["result"])
    state = quota_state_from_snapshot(snapshot, status=QuotaStatus.OK, message="ok")
    assert state.five_hour.used_percent == 10
    assert state.five_hour.remaining_percent == 90
    assert state.week.remaining_percent == 100


def test_remaining_percent_clamps() -> None:
    assert remaining_percent(None) is None
    assert remaining_percent(0) == 100
    assert remaining_percent(100) == 0
    assert remaining_percent(140) == 0
    assert remaining_percent(-8) == 100


def test_merge_notification_keeps_missing_secondary() -> None:
    base = parse_rate_limits_result(_load("rate_limits_read.json")["result"])
    update = parse_rate_limits_result(_load("rate_limits_updated.json")["params"])
    merged = merge_rate_limit_snapshots(base, update)
    assert merged.primary is not None
    assert merged.primary.used_percent == 31
    assert merged.secondary is not None
    assert merged.secondary.used_percent == 61


def test_cache_roundtrip() -> None:
    snapshot = parse_rate_limits_result(_load("rate_limits_read.json")["result"])
    fetched = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)
    restored, restored_at = snapshot_from_cache(snapshot_to_cache(snapshot, fetched))
    assert restored is not None
    assert restored.primary is not None
    assert restored.primary.used_percent == 28
    assert restored_at is not None
    assert restored_at.astimezone(timezone.utc) == fetched


def test_format_reset_time() -> None:
    now = datetime(2026, 8, 27, 16, 0)
    same_day = now.replace(hour=19, minute=20)
    tomorrow = now + timedelta(days=1)
    later = now + timedelta(days=4)
    assert format_reset_time(None, now) == "N/A"
    assert format_reset_time(same_day, now) == "今天 周四 19:20"
    assert format_reset_time(tomorrow, now) == "明天 周五 16:00"
    assert format_reset_time(later, now) == "8月31日 周一 16:00"


def test_backoff_and_stale_threshold() -> None:
    assert next_backoff_seconds(0) == 1
    assert next_backoff_seconds(1) == 2
    assert next_backoff_seconds(8) == 256
    assert next_backoff_seconds(12) == 300
    assert stale_after_seconds(15) == 120
    assert stale_after_seconds(60) == 120
    assert stale_after_seconds(300) == 600
