from datetime import datetime, timedelta, timezone

import pytest

from codexio.server_estimation import estimate_server_weeks
from codexio.estimate_display import estimate_amount, estimate_history, select_estimates

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
RESET = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)


def stamp(day, hour=0, minute=0, second=0):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=timezone.utc)


def observation(day, pct, hour=0, **changes):
    row = dict(account_key="account-a", timestamp=stamp(day, hour).isoformat(),
               reset_at=RESET.timestamp(), plan_type="pro", capacity_key="pro-20", limit_id="codex",
               used_percent=pct, window_seconds=604800)
    row.update(changes)
    return row


def daily(day, credits, **changes):
    return dict(account_key="account-a", date=stamp(day).date().isoformat(), credits=credits,
                scope_ok=True, fetched_at=NOW.isoformat(), **changes)


def estimates(obs, days, **kwargs):
    return estimate_server_weeks(obs, days, "account-a", now=kwargs.pop("now", NOW), **kwargs)


def test_aligned_credits_and_percentage_produce_point_with_rounding_bounds():
    row, = estimates([observation(2, 10), observation(5, 70)], [daily(2, 1000), daily(3, 2000), daily(4, 3000)])
    assert row["status"] == "ready" and row["method"] == "server_aligned"
    assert row["estimated_total_usd"] == 400
    assert row["low_usd"] == pytest.approx(24000 / 61)
    assert row["high_usd"] == pytest.approx(24000 / 59)
    assert len(row["full_days"]) == 3 and not row["edge_days"]


def test_multi_day_range_counts_entire_edge_days_without_hour_allocation():
    days = [daily(1, 2000), daily(2, 6000), daily(3, 6500), daily(4, 3000)]
    row, = estimates([observation(1, 10, 12), observation(4, 60, 12)], days)
    assert row["method"] == "server_range" and row["estimated_total_usd"] is None
    assert row["credits_low"] == 12500 and row["credits_high"] == 17500
    assert row["low_usd"] == pytest.approx(50000 / 51)
    assert row["high_usd"] == pytest.approx(70000 / 49)
    assert row["edge_days"] == ["2026-09-01", "2026-09-04"]
    assert " – " in estimate_amount(row)


def test_day_boundary_requires_two_close_bracketing_samples_at_same_percent():
    obs = [observation(1, 10, 23, timestamp=stamp(1, 23, 59).isoformat()),
           observation(2, 10, timestamp=stamp(2, 0, 1).isoformat()),
           observation(3, 50, 23, timestamp=stamp(3, 23, 59).isoformat()),
           observation(4, 50, timestamp=stamp(4, 0, 1).isoformat())]
    days = [daily(d, 1000) for d in range(1, 5)]
    row, = estimates(obs, days)
    assert row["method"] == "server_aligned" and row["estimated_total_usd"] == 200
    obs[1]["used_percent"] = 11
    row, = estimates(obs, days)
    assert row["method"] == "server_range"


def test_sample_crossing_midnight_is_an_edge_including_network_time():
    obs = [observation(2, 10, timestamp=stamp(2, 0, 0, 5).isoformat(), sample_start=stamp(1, 23, 59, 55).isoformat()),
           observation(4, 50)]
    row, = estimates(obs, [daily(d, 1000) for d in range(1, 5)])
    assert row["method"] == "server_range"
    assert row["full_days"] == ["2026-09-03"]
    assert row["edge_days"] == ["2026-09-01", "2026-09-02"]


@pytest.mark.parametrize("issue,status", [("missing", "missing_daily"), ("immature", "pending_daily"),
                                          ("old_fetch", "pending_daily"), ("spark", "mixed_scope")])
def test_missing_partial_or_mixed_scope_days_never_become_zero(issue, status):
    obs = [observation(2, 10), observation(4, 50)]
    days = [daily(2, 1000), daily(3, 1000)]
    now = NOW
    if issue == "missing":
        days.pop()
    elif issue == "immature":
        now = stamp(4, 23)
    elif issue == "old_fetch":
        days[1]["fetched_at"] = stamp(3, 12).isoformat()
    else:
        days[1]["scope_ok"] = False
    row, = estimates(obs, days, now=now)
    assert row["status"] == status and row["low_usd"] is None


def test_backfill_and_correction_recalculate_both_value_and_missing_status():
    obs = [observation(2, 10), observation(4, 50)]
    days = [daily(2, 1000)]
    assert estimates(obs, days)[0]["status"] == "missing_daily"
    days.append(daily(3, 1000))
    assert estimates(obs, days)[0]["estimated_total_usd"] == 200
    days[1]["credits"] = 3000
    assert estimates(obs, days)[0]["estimated_total_usd"] == 400


def test_conflicting_intervals_block_value_instead_of_picking_favorite():
    row, = estimates([observation(2, 10), observation(3, 30), observation(4, 50)],
                     [daily(2, 1000), daily(3, 10000)])
    assert row["status"] == "inconsistent"
    assert row["estimated_total_usd"] is None and row["low_usd"] is None


@pytest.mark.parametrize("field,value", [("plan_type", "plus"), ("capacity_key", "pro-5"),
                                          ("reset_at", RESET.timestamp()+600), ("used_percent", 5)])
def test_plan_capacity_reset_or_regression_split_segments(field, value):
    rows = estimates([observation(2, 10), observation(4, 50, **{field: value})],
                     [daily(2, 1000), daily(3, 1000)])
    assert len(rows) == 2 and all(r["status"] != "ready" for r in rows)
    assert sum(r["is_closed"] for r in rows) == 1


def test_accounts_buckets_and_old_unverified_current_tags_are_never_combined():
    obs = [observation(2, 10), observation(4, 50, account_key="current"), observation(4, 50, limit_id="codex_bengalfox")]
    assert estimates(obs, [daily(2, 1000), daily(3, 1000)])[0]["status"] != "ready"
    obs = [observation(2, 10), observation(4, 50)]
    days = [dict(daily(d, 1000), account_key="account-b") for d in (2, 3)]
    assert estimates(obs, days)[0]["status"] == "missing_daily"


def test_unstarted_zero_deadlines_do_not_make_phantom_cycles():
    obs = [observation(5, 0, timestamp=(stamp(5)+timedelta(minutes=i)).isoformat(),
                       reset_at=(stamp(5)+timedelta(days=7, minutes=i)).timestamp()) for i in range(20)]
    assert estimates(obs, []) == []


@pytest.mark.parametrize("pct", [11, 19, 100])
def test_too_little_delta_or_saturated_end_does_not_extrapolate(pct):
    row, = estimates([observation(2, 10), observation(4, pct)], [daily(2, 1000), daily(3, 1000)])
    assert row["status"] == "insufficient" and row["estimated_total_usd"] is None


def test_short_local_window_remains_reference_and_server_is_primary_when_ready():
    local = dict(observation(2, 10), estimated_total_usd=123, start=stamp(2).isoformat(), end=stamp(4).isoformat())
    ctx = dict(account_key="account-a", quota_status="ok", plan_type="pro", reset_at=RESET.timestamp(),
               last_quota_at=NOW.isoformat(), last_daily_at=NOW.isoformat(), daily_status="ok")
    data = dict(weekly_estimates=[local], server_usage_context=ctx)
    assert select_estimates(data, NOW)["primary"] is local
    data["weekly_server_estimates"] = estimates([observation(2, 10), observation(4, 50)], [daily(2, 1000), daily(3, 1000)])
    selected = select_estimates(data, NOW)
    assert selected["primary"]["method"] == "server_aligned" and selected["reference"] is local
    ctx["quota_status"] = "network_error"
    assert select_estimates(data, NOW)["cached"]
    ctx["quota_status"] = "account_mismatch"
    assert not select_estimates(data, NOW)["server"]


def test_closed_cycles_and_spark_are_not_selected_as_current():
    rows = [dict(observation(2, 0, limit_id="codex_bengalfox"), estimated_total_usd=456),
            dict(observation(2, 10), estimated_total_usd=789, termination="early_reset")]
    assert select_estimates(dict(weekly_estimates=rows), NOW)["primary"] == {}
    assert len(estimate_history(dict(weekly_estimates=rows))) == 1


def test_more_samples_do_not_sum_overlapping_daily_cost_twice():
    obs = [observation(day, 10+(day-2)*20) for day in (2, 3, 4, 5)]
    row, = estimates(obs, [daily(d, 1000) for d in (2, 3, 4)])
    assert row["estimated_total_usd"] == 200
    assert row["checked_intervals"] == 6
