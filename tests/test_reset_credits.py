from datetime import datetime, timezone

import pytest

from aiquota.rate_limits import (
    QuotaState,
    QuotaStatus,
    RateLimitSnapshot,
    merge_rate_limit_snapshots,
    parse_rate_limits_result,
    quota_state_from_snapshot,
    snapshot_from_cache,
    snapshot_to_cache,
)


def _result(credits=2, *, by_limit=False):
    result = {
        "rateLimits": {"limitId": "codex", "primary": {"usedPercent": 28.75}},
        "rateLimitResetCredits": {"availableCount": credits, "credits": [{"id": "one"}]},
    }
    if by_limit:
        result["rateLimitsByLimitId"] = {
            "codex": {"limitId": "codex", "primary": {"usedPercent": 32.25}},
            "other": {"limitId": "other", "primary": {"usedPercent": 80}},
        }
    return result


@pytest.mark.parametrize("by_limit", [False, True])
def test_account_reset_count_survives_bucket_selection_and_cache(by_limit):
    snapshot = parse_rate_limits_result(_result(2, by_limit=by_limit))
    assert snapshot.reset_credits == 2
    assert snapshot.primary.used_percent == (32.25 if by_limit else 28.75)
    state = quota_state_from_snapshot(snapshot, status=QuotaStatus.STALE, message="数据过期")
    assert state.reset_credits == 2
    assert state.status == QuotaStatus.STALE
    saved_at = datetime(2026, 9, 7, tzinfo=timezone.utc)
    cached = snapshot_to_cache(snapshot, saved_at)
    assert cached["rateLimitResetCredits"] == {"availableCount": 2}
    restored, timestamp = snapshot_from_cache(cached)
    assert restored.reset_credits == 2
    assert timestamp == saved_at
    assert restored.primary.used_percent == snapshot.primary.used_percent
    if by_limit:
        assert all(bucket.by_limit is None for bucket in restored.by_limit.values())


def test_reset_count_snake_case_zero_and_authoritative_count():
    snapshot = parse_rate_limits_result({
        "rate_limits": {},
        "rate_limit_reset_credits": {"available_count": 0, "credits": [{"id": "ignored"}]},
    })
    assert snapshot.reset_credits == 0
    assert quota_state_from_snapshot(snapshot, status=QuotaStatus.OK, message="ok").reset_credits == 0


@pytest.mark.parametrize("credits", [None, [], {}, {"credits": [{"id": "one"}]},
    {"availableCount": None}, {"availableCount": -1}, {"availableCount": "bad"},
    {"availableCount": True}, {"availableCount": 1.5}, {"availableCount": float("nan")},
    {"availableCount": float("inf")}])
def test_unavailable_or_invalid_reset_count_is_unknown(credits):
    result = _result()
    result["rateLimitResetCredits"] = credits
    snapshot = parse_rate_limits_result(result)
    assert snapshot.reset_credits is None
    assert snapshot._reset_credits_present


@pytest.mark.parametrize("by_limit", [False, True])
def test_partial_notifications_preserve_count_but_explicit_null_clears(by_limit):
    base = parse_rate_limits_result(_result(3, by_limit=by_limit))
    partial = parse_rate_limits_result({"rateLimits": {
        "limitId": "codex", "primary": {"usedPercent": 45.5},
    }})
    merged = merge_rate_limit_snapshots(base, partial)
    assert merged.reset_credits == 3
    assert merged.primary.used_percent == 45.5
    explicit_null = parse_rate_limits_result({"rateLimits": {}, "rateLimitResetCredits": None})
    cleared = merge_rate_limit_snapshots(merged, explicit_null)
    assert cleared.reset_credits is None
    restored, _ = snapshot_from_cache(snapshot_to_cache(cleared, datetime.now(timezone.utc)))
    assert restored.reset_credits is None
    assert restored._reset_credits_present
    assert merge_rate_limit_snapshots(base, restored).reset_credits is None


def test_other_bucket_notification_updates_account_count_without_switching_quota():
    base = parse_rate_limits_result(_result(3, by_limit=True))
    update = parse_rate_limits_result({
        "rateLimits": {"limitId": "other", "primary": {"usedPercent": 88.25}},
        "rateLimitResetCredits": {"availableCount": 1, "credits": None},
    })
    merged = merge_rate_limit_snapshots(base, update)
    assert merged.limit_id == "codex"
    assert merged.primary.used_percent == 32.25
    assert merged.by_limit["other"].primary.used_percent == 88.25
    assert merged.reset_credits == 1
    missing = parse_rate_limits_result({"rateLimitsByLimitId": {"other": {"planType": "pro"}}})
    assert merge_rate_limit_snapshots(merged, missing).reset_credits == 1


def test_legacy_cache_and_default_constructors_remain_unknown():
    assert QuotaState.empty().reset_credits is None
    assert RateLimitSnapshot().reset_credits is None
    restored, _ = snapshot_from_cache({"fetched_at": "2026-09-07T00:00:00Z", "rateLimits": {}})
    assert restored.reset_credits is None
    assert not restored._reset_credits_present
    # Direct constructors can provide a known count without the internal presence flag.
    known = RateLimitSnapshot(reset_credits=4)
    assert merge_rate_limit_snapshots(restored, known).reset_credits == 4
    assert merge_rate_limit_snapshots(known, restored).reset_credits == 4


def test_bare_count_notification_and_nested_compatibility():
    base = parse_rate_limits_result(_result(3, by_limit=True))
    notification = RateLimitSnapshot.from_payload({"rateLimitResetCredits": {"availableCount": 0}})
    assert merge_rate_limit_snapshots(base, notification).reset_credits == 0
    nested = parse_rate_limits_result({"rateLimits": {
        "rate_limit_reset_credits": {"available_count": 2},
    }})
    assert nested.reset_credits == 2
