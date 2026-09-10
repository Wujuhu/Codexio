from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from codexio.estimation import estimate_weeks

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)
RESET = BASE + timedelta(days=7)


def quota(hours, used, **changes):
    result = dict(timestamp=(BASE + timedelta(hours=hours)).isoformat(), used_percent=used,
                  window_minutes=10080, resets_at=int(RESET.timestamp()), plan_type="pro",
                  limit_id="codex", source_id="local", account_key="unknown")
    result.update(changes)
    return result


def request(hours, cost=10, **changes):
    result = dict(timestamp=(BASE + timedelta(hours=hours)).isoformat(), cost_usd=cost,
                  pricing_status="priced", total_tokens=10000, model="gpt-6-astra", provider="openai",
                  source_id="local", limit_id="codex", id=str(hours))
    result.update(changes)
    return result


def estimate(records, observations, **options):
    return estimate_weeks(records, observations, options.pop("account_since", BASE.isoformat()),
                          now=options.pop("now", BASE + timedelta(hours=24)), **options)


def test_matched_delta_not_all_history_and_rounding_range():
    rows = estimate([request(0, 1000), request(2, 12), request(3, 8)], [quota(1, 10), quota(4, 15)])
    row = rows[0]
    assert row["status"] == "ready"
    assert row["consumed_usd"] == 20
    assert row["estimated_total_usd"] == 400
    assert row["estimated_remaining_usd"] == 340
    assert row["rounding_low_usd"] == pytest.approx(2000/6)
    assert row["rounding_high_usd"] == 500


def test_percent_span_and_maturity_gates():
    assert estimate([request(2)], [quota(1, 10), quota(4, 11.99)])[0]["estimated_total_usd"] is None
    assert estimate([request(2)], [quota(1, 10), quota(4, 12)])[0]["estimated_total_usd"] == 500
    assert estimate([request(2)], [quota(1, 10), quota(24, 20)])[0]["status"] == "collecting"


def test_reset_second_jitter_coalesces_but_early_reset_splits():
    rows = estimate([request(2)], [quota(1, 10), quota(4, 20, resets_at=int(RESET.timestamp()) + 4)])
    assert len(rows) == 1
    assert rows[0]["estimated_total_usd"] == 100
    rows = estimate([request(2), request(10, 100)], [quota(1, 10), quota(4, 20),
                    quota(8, 0, resets_at=int(RESET.timestamp()) + 8*3600)])
    assert len(rows) == 2
    assert rows[-1]["termination"] == "early_reset"
    assert rows[-1]["consumed_usd"] == 10


def test_unknown_history_requires_source_time_assignment():
    since = (BASE + timedelta(hours=12)).isoformat()
    observations = [quota(1, 10), quota(4, 20)]
    row = estimate([request(2)], observations, account_since=since)[0]
    assert row["status"] == "unattributed"
    assert row["consumed_usd"] == 10
    assignment = dict(source_id="local", start=BASE.isoformat(), end=since, account_key="current")
    row = estimate([request(2)], observations, account_since=since, assignments=[assignment])[0]
    assert row["status"] == "ready"


def test_plan_and_bucket_changes_never_mix():
    rows = estimate([request(2), request(5)], [quota(1, 5, plan_type="plus"), quota(3, 10, plan_type="plus"),
                    quota(4, 20), quota(6, 25), quota(5, 0, limit_id="codex_bengalfox")])
    ready = [r for r in rows if r["status"] == "ready"]
    assert len(ready) == 2
    assert all(r["estimated_total_usd"] == 200 for r in ready)


def test_missing_prices_or_incomplete_sync_do_not_extrapolate():
    observations = [quota(1, 10), quota(4, 20)]
    records = [request(2), request(3, None, pricing_status="unpriced")]
    assert estimate(records, observations)[0]["status"] == "unpriced"
    assert estimate([request(2)], observations, sources_complete=False)[0]["status"] == "incomplete"
    # A subsequent price/log backfill recalculates, rather than freezing the old result.
    records[-1]["cost_usd"], records[-1]["pricing_status"] = 20, "priced"
    assert estimate(records, observations)[0]["estimated_total_usd"] == 300


def test_unpriced_gap_can_restart_later_valid_interval():
    records = [request(2, None, pricing_status="unpriced"), request(5)]
    row = estimate(records, [quota(1, 0), quota(3, 10), quota(6, 20)])[0]
    assert row["status"] == "ready"
    assert row["start"] == quota(3, 0)["timestamp"]
    assert row["estimated_total_usd"] == 100


def test_saturation_does_not_inflate_estimate_with_extra_usage():
    row = estimate([request(2), request(5, 999), request(7, 999)],
                   [quota(1, 80), quota(3, 90), quota(6, 100), quota(8, 100)])[0]
    assert row["estimated_total_usd"] == 100
    assert row["estimated_remaining_usd"] == 0


def test_stale_regression_is_ignored_and_confirmed_regression_splits():
    records = [request(2), request(5)]
    rows = estimate(records, [quota(1, 10), quota(3, 20), quota(4, 15), quota(6, 30)])
    assert len(rows) == 1
    assert rows[0]["estimated_total_usd"] == 100
    rows = estimate(records, [quota(1, 10), quota(3, 20), quota(4, 0), quota(6, 10)])
    assert len(rows) == 2
    assert all(row["estimated_total_usd"] == 100 for row in rows)


def test_no_complete_week_claim_from_partial_history():
    row = estimate([request(30)], [quota(25, 7), quota(100, 50)], now=BASE + timedelta(days=9))[0]
    assert row["is_closed"]
    assert row["coverage"] == "observed_interval"
    assert row["status"] == "ready"


def test_dedup_and_other_provider_requests_excluded():
    record = request(2)
    row = estimate([record, record, request(3, 1000, provider="custom")], [quota(1, 10), quota(4, 20)])[0]
    assert row["consumed_usd"] == 10


def test_stale_paired_snapshot_does_not_become_endpoint():
    row = estimate([request(2)], [quota(1, 10), quota(4, 20, pairing_delay_seconds=600)])[0]
    assert row["estimated_total_usd"] is None


def test_reference_prices_produce_a_labelled_projection_from_two_points():
    row = estimate([request(2, 10, pricing_status="estimated")], [quota(1, 10), quota(4, 20)])[0]
    assert row["consumed_usd"] == 10
    assert row["status"] == "estimated_prices"
    assert row["estimated_total_usd"] == 100
    assert row["estimated_price_requests"] == 1 and "参考单价" in row["reason"]
    small = estimate([request(2, 10, pricing_status="estimated")], [quota(1, 10), quota(4, 12)])[0]
    assert small["status"] == "estimated_prices" and small["estimated_total_usd"] == 500


def test_unknown_quota_scope_cannot_be_silently_put_in_default_bucket():
    row = estimate([request(2, 10), request(3, 100, limit_id=None)], [quota(1, 10), quota(4, 20)])[0]
    assert row["status"] == "unknown_limit"
    assert row["estimated_total_usd"] is None
