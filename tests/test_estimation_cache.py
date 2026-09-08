from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aiquota.estimation import estimate_weeks
from aiquota.pricing import PricingCatalog
from aiquota.usage_queries import UsageQueries
from aiquota.usage_store import UsageStore

BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)
NOW = BASE + timedelta(days=19, hours=12)
SINCE = (BASE - timedelta(days=1)).isoformat()


def call(ident, stamp, **changes):
    row = dict(id=ident, timestamp=stamp.isoformat(), session_id="test", turn_id=ident,
               model="gpt-6-astra", provider="openai", service_tier="default", limit_id="codex",
               account_key="current", input_tokens=100000, cached_input_tokens=0,
               cache_write_input_tokens=0, output_tokens=1000, total_tokens=101000,
               reasoning_output_tokens=0, quality="response")
    row.update(changes)
    return row


def quota(ident, stamp, reset, used, **changes):
    row = dict(id=ident, timestamp=stamp.isoformat(), resets_at=reset.timestamp(), used_percent=used,
               window_minutes=10080, plan_type="pro", limit_id="codex", account_key="current")
    row.update(changes)
    return row


def setup(tmp_path):
    store = UsageStore(tmp_path / "usage.sqlite")
    catalog = PricingCatalog(tmp_path / "prices")
    queries = UsageQueries(store.path)
    for index in range(3):
        start = BASE + timedelta(days=index * 7, hours=1)
        reset = BASE + timedelta(days=(index + 1) * 7)
        end = reset - timedelta(hours=6) if index < 2 else NOW - timedelta(hours=1)
        store.upsert_records([call("call-%d" % index, start + timedelta(hours=1))], "local")
        store.upsert_observations([quota("q%d-start" % index, start, reset, 0 if index < 2 else 10),
                                   quota("q%d-end" % index, end, reset, 20)], "local")
    queries.rebuild(catalog)
    return store, catalog, queries


def reference(queries, now=NOW, active=("local",), since=SINCE, complete=True, assignments=()):
    return estimate_weeks(queries.calibration_records(active), queries.calibration_observations(active), since,
                          now=now, sources_complete=complete, assignments=assignments)


def check(queries, now=NOW, active=("local",), since=SINCE, complete=True, assignments=()):
    expected = reference(queries, now, active, since, complete, assignments)
    actual = queries.weekly_estimates(active, since, now=now, sources_complete=complete, assignments=assignments)
    assert actual == expected
    return actual


def test_current_cycle_only_recomputes_for_append_maturity_and_reset(tmp_path):
    store, catalog, queries = setup(tmp_path)
    original = check(queries)
    assert queries.last_estimation_stats["evaluated_cycles"] == 3
    assert queries.last_estimation_stats["records_read"] == 3
    assert original[-1]["coverage"] == "closed_cycle_observed"
    check(queries, NOW + timedelta(minutes=1))
    assert queries.last_estimation_stats["evaluated_cycles"] == 0
    assert queries.last_estimation_stats["records_read"] == 0
    assert queries.last_estimation_stats["observations_read"] == 0
    reset = BASE + timedelta(days=21)
    store.upsert_observations([quota("current-new", NOW + timedelta(seconds=30), reset, 25)], "local")
    appended = check(queries, NOW + timedelta(minutes=1))
    assert appended[1:] == original[1:]
    assert queries.last_estimation_stats["evaluated_resets"] == [int(reset.timestamp())]
    assert queries.last_estimation_stats["records_read"] == 1
    check(queries, NOW + timedelta(minutes=3))
    assert queries.last_estimation_stats["evaluated_resets"] == [int(reset.timestamp())]
    check(queries, reset + timedelta(minutes=1))
    assert queries.last_estimation_stats["evaluated_resets"] == [int(reset.timestamp())]
    check(queries, reset + timedelta(days=1))
    assert queries.last_estimation_stats["evaluated_cycles"] == 0


def test_cache_survives_reopen_and_new_cycle_reuses_closed_predecessor(tmp_path):
    store, catalog, queries = setup(tmp_path)
    check(queries)
    reopened = UsageQueries(store.path)
    check(reopened)
    assert reopened.last_estimation_stats["evaluated_cycles"] == 0
    closed_at = BASE + timedelta(days=21, minutes=1)
    check(reopened, closed_at)
    next_time = BASE + timedelta(days=21, hours=1)
    next_reset = BASE + timedelta(days=28)
    store.upsert_observations([quota("next-cycle", next_time, next_reset, 0)], "local")
    check(reopened, next_time + timedelta(minutes=3))
    assert reopened.last_estimation_stats["evaluated_resets"] == [int(next_reset.timestamp())]
    assert reopened.last_estimation_stats["reused_cycles"] == 3
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM usage_estimation_changes").fetchone()[0] == 0


def test_late_record_timestamp_move_alias_and_delete_invalidate_only_overlaps(tmp_path):
    store, catalog, queries = setup(tmp_path)
    original = check(queries)
    first_reset = BASE + timedelta(days=7)
    second_reset = BASE + timedelta(days=14)
    late = call("late", BASE + timedelta(hours=3))
    store.upsert_records([late], "ssh:inactive")
    queries.rebuild(catalog)
    assert check(queries) == original
    store.upsert_records([late], "local")
    queries.rebuild(catalog)
    changed = check(queries)
    assert queries.last_estimation_stats["evaluated_resets"] == [int(first_reset.timestamp())]
    assert changed[-1]["consumed_usd"] > original[-1]["consumed_usd"]
    store.delete_source_records(["late"], "ssh:inactive")
    moved = dict(late, timestamp=(BASE + timedelta(days=8)).isoformat())
    store.upsert_records([moved], "local")
    queries.rebuild(catalog)
    check(queries)
    assert set(queries.last_estimation_stats["evaluated_resets"]) == {int(first_reset.timestamp()), int(second_reset.timestamp())}
    store.delete_source_records(["late"], "local")
    queries.rebuild(catalog)
    assert check(queries) == original
    assert queries.last_estimation_stats["evaluated_resets"] == [int(second_reset.timestamp())]


def test_late_observation_boundary_delete_and_regression_preserve_oracle(tmp_path):
    store, catalog, queries = setup(tmp_path)
    check(queries)
    first_reset = BASE + timedelta(days=7)
    store.upsert_observations([quota("late-mid", BASE + timedelta(hours=6), first_reset, 10)], "local")
    check(queries)
    assert queries.last_estimation_stats["evaluated_resets"] == [int(first_reset.timestamp())]
    assert queries.last_estimation_stats["reused_cycles"] == 2
    # An inserted reset changes the boundaries of the historical tail, including
    # the old cycle's termination and a return to its previous reset later on.
    store.upsert_observations([quota("late-reset", BASE + timedelta(hours=12), BASE + timedelta(days=5), 0)], "local")
    check(queries)
    store.delete_source_observations(["late-reset"], "local")
    check(queries)
    store.upsert_observations([quota("late-mid", BASE + timedelta(hours=6), first_reset, 0)], "local")
    check(queries)
    store.delete_source_observations(["q0-start"], "local")
    check(queries)


def test_plan_bucket_account_source_price_and_completeness_changes_are_exact(tmp_path):
    store, catalog, queries = setup(tmp_path)
    check(queries)
    for complete in (False, True):
        check(queries, complete=complete)
        assert queries.last_estimation_stats["evaluated_cycles"] == 3
    catalog.set_override("gpt-6-astra", dict(input=2, cache_read=.2, cache_write=2, output=4))
    queries.rebuild(catalog)
    check(queries)
    assert queries.last_estimation_stats["evaluated_cycles"] == 3
    since = (BASE + timedelta(days=8)).isoformat()
    check(queries, since=since)
    assignments = [dict(source_id="local", start=SINCE, end=since, account_key="current")]
    check(queries, since=since, assignments=assignments)
    assert check(queries, active=()) == []
    check(queries)
    reset = BASE + timedelta(days=21)
    store.upsert_observations([quota("plan-change", NOW - timedelta(minutes=30), reset, 25, plan_type="plus"),
                               quota("other-bucket", NOW - timedelta(minutes=20), reset, 1, limit_id="other")], "local")
    store.upsert_records([call("unknown-scope", NOW - timedelta(minutes=45), limit_id=None)], "local")
    queries.rebuild(catalog)
    check(queries)


def test_future_observation_and_clock_rollback_recompute_at_correct_boundary(tmp_path):
    store, catalog, queries = setup(tmp_path)
    check(queries)
    reset = BASE + timedelta(days=21)
    future = NOW + timedelta(hours=1)
    store.upsert_observations([quota("future", future, reset, 30)], "local")
    check(queries)
    assert queries.last_estimation_stats["evaluated_cycles"] == 0
    check(queries, future + timedelta(minutes=3))
    assert queries.last_estimation_stats["evaluated_resets"] == [int(reset.timestamp())]
    check(queries, NOW)
    check(queries, future + timedelta(minutes=3))


def test_duplicate_and_same_timestamp_observations_keep_stable_cycle_order(tmp_path):
    store, catalog, queries = setup(tmp_path)
    check(queries)
    stamp, reset = BASE + timedelta(hours=4), BASE + timedelta(days=7)
    values = [quota("a-same-time", stamp, reset, 5, plan_type="plus"),
              quota("b-same-time", stamp, reset, 6),
              quota("c-same-time", stamp, reset, 7, plan_type="plus")]
    store.upsert_observations(values, "local")
    store.upsert_observations([values[0]], "ssh:copy")
    check(queries, active=("local", "ssh:copy"))
    store.delete_source_observations(["b-same-time"], "local")
    check(queries, active=("local", "ssh:copy"))
    store.delete_source_observations(["a-same-time"], "local")
    check(queries, active=("local", "ssh:copy"))


def test_live_cache_reads_are_bounded_and_short_window_samples_do_not_read_history(tmp_path, monkeypatch):
    store, catalog, queries = setup(tmp_path)
    expected = reference(queries)
    real_records = queries.calibration_records
    ranges = []
    def records(active, start=None, end=None):
        assert start is not None and end is not None
        ranges.append((start, end))
        yield from real_records(active, start=start, end=end)
    monkeypatch.setattr(queries, "calibration_records", records)
    monkeypatch.setattr(queries, "calibration_observations", lambda *_: pytest.fail("full observation history reader used"))
    assert queries.weekly_estimates(("local",), SINCE, now=NOW) == expected
    assert len(ranges) == 3
    ranges.clear()
    store.upsert_observations([quota("five-hour", NOW - timedelta(minutes=1), NOW + timedelta(hours=4), 12, window_minutes=300)], "local")
    assert queries.weekly_estimates(("local",), SINCE, now=NOW) == expected
    assert ranges == []


def test_scoped_calibration_reads_use_time_index_instead_of_all_source_ids(tmp_path, monkeypatch):
    store, catalog, queries = setup(tmp_path)
    statements = []
    connect = sqlite3.connect
    def traced_connection(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr("aiquota.usage_queries.sqlite3.connect", traced_connection)
    rows = list(queries.calibration_records(("local",), start=BASE, end=BASE + timedelta(days=7)))
    assert [row["id"] for row in rows] == ["call-0"]
    sql = next(statement for statement in statements if statement.startswith("SELECT c.metrics"))
    with connect(str(store.path)) as db:
        plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql)]
    assert any("priced_calls_time" in step and "timestamp>?" in step for step in plan)
    assert not any("LIST SUBQUERY" in step for step in plan)


def test_failed_cache_update_rolls_back_results_and_retains_change_journal(tmp_path, monkeypatch):
    store, catalog, queries = setup(tmp_path)
    before = check(queries)
    store.upsert_records([call("late", BASE + timedelta(hours=3))], "local")
    queries.rebuild(catalog)
    import aiquota.estimation_cache as cache_module
    original = cache_module.estimate_weeks
    monkeypatch.setattr(cache_module, "estimate_weeks", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError):
        queries.weekly_estimates(("local",), SINCE, now=NOW)
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM usage_estimation_changes").fetchone()[0] > 0
        cached = [result for row in db.execute("SELECT outputs FROM usage_estimation_cycles") for result in json.loads(row[0])]
    assert sorted(cached, key=lambda r: (r["reset_at"], r["end"]), reverse=True) == before
    monkeypatch.setattr(cache_module, "estimate_weeks", original)
    check(queries)


def test_writes_after_priced_snapshot_keep_record_events_until_next_materialization(tmp_path):
    store, catalog, queries = setup(tmp_path)
    original = check(queries)
    queries.rebuild(catalog)
    # Simulate a second collector writing between the materialization and cache
    # transactions. Observation updates may proceed; unpriced record events must
    # survive until the corresponding priced snapshot exists.
    store.upsert_records([call("concurrent-late", BASE + timedelta(hours=3))], "local")
    reset = BASE + timedelta(days=21)
    store.upsert_observations([quota("concurrent-quota", NOW - timedelta(minutes=5), reset, 25)], "local")
    interim = check(queries)
    assert interim[-1] == original[-1]
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM usage_estimation_changes WHERE kind='record' AND item_id='concurrent-late'").fetchone()[0] > 0
        assert db.execute("SELECT COUNT(*) FROM usage_estimation_changes WHERE kind='observation'").fetchone()[0] == 0
    queries.rebuild(catalog)
    updated = check(queries)
    assert updated[-1]["consumed_usd"] > original[-1]["consumed_usd"]
    assert queries.last_estimation_stats["evaluated_resets"] == [int((BASE + timedelta(days=7)).timestamp())]
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM usage_estimation_changes").fetchone()[0] == 0


def test_existing_priced_index_initializes_missing_journal_watermark_without_rebuild(tmp_path):
    store, catalog, queries = setup(tmp_path)
    check(queries)
    store.upsert_records([call("late", BASE + timedelta(hours=3))], "local")
    generation = queries.rebuild(catalog)
    with store._connect() as db:
        db.execute("DELETE FROM usage_query_state WHERE key='materialized_change_seq'")
    assert queries.rebuild(catalog) == generation
    with store._connect() as db:
        value = db.execute("SELECT data FROM usage_query_state WHERE key='materialized_change_seq'").fetchone()
        assert value and int(value[0]) > 0
    check(queries)
    assert queries.last_estimation_stats["evaluated_resets"] == [int((BASE + timedelta(days=7)).timestamp())]


def test_worker_skips_unchanged_minute_summary_scans_but_honors_time_boundaries(tmp_path, monkeypatch):
    from aiquota.analytics_config import default_config
    from aiquota.usage_worker import UsageWorker
    import aiquota.usage_worker as worker_module
    store = UsageStore(tmp_path / "summary.sqlite")
    catalog = PricingCatalog(tmp_path / "prices")
    start = BASE + timedelta(hours=3)
    store.upsert_records([call("past", start - timedelta(hours=1)), call("future", start + timedelta(hours=1))], "local")
    worker = UsageWorker(dict(default_config(), account_since=SINCE, codex_roots=[], auto_sync_prices=False), mock=True, directory=tmp_path)
    worker._store, worker._catalog = store, catalog
    class Clock(datetime):
        current = start
        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.astimezone()
    monkeypatch.setattr(worker_module, "datetime", Clock)
    calls = []
    summarize = UsageQueries.summaries
    def counted_summary(self, now=None):
        calls.append(now)
        return summarize(self, now)
    monkeypatch.setattr(UsageQueries, "summaries", counted_summary)
    published = []
    worker.data_changed.connect(published.append)
    worker._publish()
    assert len(calls) == 1 and published[-1]["summaries"]["all"]["requests"] == 1
    Clock.current = start + timedelta(minutes=1)
    worker._publish()
    assert len(calls) == 1
    Clock.current = start + timedelta(hours=1)
    worker._publish()
    assert len(calls) == 2 and published[-1]["summaries"]["all"]["requests"] == 2
    Clock.current = start + timedelta(minutes=1)
    worker._publish()
    assert len(calls) == 3 and published[-1]["summaries"]["all"]["requests"] == 1
    Clock.current = start + timedelta(days=1)
    worker._publish()
    assert len(calls) == 4 and published[-1]["summaries"]["all"]["requests"] == 2
    assert published[-1]["summaries"]["today"]["requests"] == 0
