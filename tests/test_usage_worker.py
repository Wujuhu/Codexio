from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aiquota.analytics_config import default_config
from aiquota.pricing import PricingCatalog
from aiquota.rate_limits import RateLimitSnapshot
from aiquota.usage_store import UsageStore
from aiquota.usage_queries import UsageQueries
from aiquota.usage_worker import UsageWorker, _count, parse_time, summarize


def worker_at(tmp_path, mock=False, **config):
    settings = default_config()
    settings.update(codex_roots=[str(tmp_path)], auto_sync_prices=False,
                    account_since=(datetime.now(timezone.utc) - timedelta(days=35)).isoformat())
    settings.update(config)
    worker = UsageWorker(settings, mock=mock, directory=tmp_path)
    worker._store = UsageStore(tmp_path / "usage.sqlite")
    worker._catalog = PricingCatalog(tmp_path / "prices")
    return worker


def record(stamp, **changes):
    row = dict(id="one", session_id="session", model="gpt-6-astra", provider="openai", limit_id="codex",
               timestamp=stamp.isoformat(), service_tier="default", input_tokens=1000, cached_input_tokens=0,
               cache_write_input_tokens=0, output_tokens=100, reasoning_output_tokens=50, total_tokens=1100,
               quality="response", source_id="local")
    row.update(changes)
    return row


def add_interval(worker):
    now = datetime.now(timezone.utc)
    worker._store.upsert_records([record(now - timedelta(hours=2))], "local")
    worker._store.set_source_status("local", status="ok", last_scan_at=now.isoformat())
    worker._store.upsert_observations([
        dict(id="q1", timestamp=(now-timedelta(hours=3)).isoformat(), used_percent=10, window_minutes=10080,
             resets_at=int((now+timedelta(days=3)).timestamp()), plan_type="pro", limit_id="codex", account_key="current"),
        dict(id="q2", timestamp=(now-timedelta(hours=1)).isoformat(), used_percent=20, window_minutes=10080,
             resets_at=int((now+timedelta(days=3)).timestamp()), plan_type="pro", limit_id="codex", account_key="current"),
    ], "local")


def publish(worker):
    output = []
    worker.data_changed.connect(output.append)
    worker._publish()
    worker.data_changed.disconnect(output.append)
    return output[-1]


def test_mock_publish_delivers_ready_estimate_even_when_local_sources_disabled(tmp_path):
    worker = worker_at(tmp_path, mock=True, codex_roots=[])
    worker._seed_mock()
    data = publish(worker)
    queries = UsageQueries(data["query_path"])
    first = queries.page("model_call")
    assert first["total"] == 420 and len(first["rows"]) == 100
    assert "records" not in data and "user_requests" not in data
    assert data["sources_complete"]
    assert data["weekly_estimates"][0]["status"] == "ready"
    assert data["weekly_estimates"][0]["estimated_total_usd"] > 0
    assert all(row["pricing_status"] == "priced" for page in range(first["pages"])
               for row in queries.page("model_call", page=page)["rows"])


def test_changing_theme_does_not_unassign_mock_history(tmp_path, monkeypatch):
    worker = worker_at(tmp_path, mock=True)
    worker.update_config(dict(worker._config, theme="dark", account_since=datetime.now(timezone.utc).isoformat()))
    output = []
    worker.data_changed.connect(output.append)
    original_publish = worker._publish
    def publish_and_stop():
        original_publish()
        worker.stop()
    monkeypatch.setattr(worker, "_publish", publish_and_stop)
    worker._run_loop(None)
    assert worker._config["theme"] == "dark"
    assert output[-1]["weekly_estimates"][0]["status"] == "ready"


def test_none_is_preserved_when_every_consuming_record_is_unpriced():
    now = datetime.now(timezone.utc)
    rows = [record(now-timedelta(minutes=1), cost_usd=None)]
    summary = summarize(rows, now)["all"]
    assert summary["tokens"] == 1100
    assert summary["usd"] is None
    assert summary["cost_status"] == "unpriced"
    assert summary["unpriced_requests"] == 1
    assert summarize([], now)["all"]["usd"] == 0


def test_64_bit_token_counters_are_not_rounded_via_float():
    value = 9007199254740993
    assert _count(value) == value
    assert _count(-1) == 0
    assert _count(True) == 0
    now = datetime.now(timezone.utc)
    assert summarize([record(now, total_tokens=value, cost_usd=None)], now)["all"]["tokens"] == value


def test_partial_costs_and_aggregate_counters_have_honest_summary():
    now = datetime.now(timezone.utc)
    rows = [record(now-timedelta(minutes=2), cost_usd=3),
            record(now-timedelta(minutes=1), id="two", cost_usd=None, quality="cumulative_observation:invalid_subsets")]
    summary = summarize(rows, now)["all"]
    assert summary["usd"] == 3
    assert summary["cost_status"] == "partial"
    assert summary["requests"] == 1
    assert summary["tokens"] == 2200


def test_disabled_source_history_remains_visible_but_not_calibrated(tmp_path):
    worker = worker_at(tmp_path, ssh_sources=[dict(id="old", host="old-host", enabled=False)])
    add_interval(worker)
    worker._store.upsert_records([record(datetime.now(timezone.utc)-timedelta(hours=2), id="remote", input_tokens=1000000, total_tokens=1000100)], "ssh:old")
    worker._store.set_source_status("ssh:old", status="error")
    data = publish(worker)
    estimate = data["weekly_estimates"][0]
    assert data["sources_complete"]
    assert estimate["status"] == "ready"
    assert estimate["consumed_usd"] == pytest.approx(.015)
    assert data["summaries"]["all"]["usd"] > estimate["consumed_usd"]


def test_enabled_failing_source_blocks_calibration(tmp_path):
    worker = worker_at(tmp_path, ssh_sources=[dict(id="ssh:peer", host="peer", enabled=True)])
    add_interval(worker)
    worker._store.set_source_status("ssh:peer", status="error")
    data = publish(worker)
    assert not data["sources_complete"]
    assert data["weekly_estimates"][0]["status"] == "incomplete"


def test_cancelled_local_scan_is_not_treated_as_complete(tmp_path):
    worker = worker_at(tmp_path)
    add_interval(worker)
    worker._store.set_source_status("local", status="ok", cancelled=True)
    data = publish(worker)
    assert data["weekly_estimates"][0]["status"] == "incomplete"


def test_cross_source_duplicate_can_calibrate_from_active_link(tmp_path):
    worker = worker_at(tmp_path)
    stamp = datetime.now(timezone.utc)-timedelta(hours=2)
    item = record(stamp, response_id="response-one")
    worker._store.upsert_records([item], "ssh:removed")
    worker._store.upsert_records([item], "local")
    add_interval(worker)
    data = publish(worker)
    assert data["sources_complete"]
    assert data["weekly_estimates"][0]["status"] == "ready"
    assert data["weekly_estimates"][0]["request_count"] == 2


def test_live_snapshot_keeps_current_account_utc_and_bucket_ids(tmp_path):
    worker = worker_at(tmp_path)
    snapshot = RateLimitSnapshot.from_payload(dict(plan_type="pro", limit_id="codex", primary={
        "used_percent": 12.5, "window_minutes": 10080, "resets_at": 1800000000}))
    worker.add_snapshot(snapshot, "local-quota")
    command, (source_id, observations) = worker._commands.get_nowait()
    assert command == "observations" and source_id == "local-quota"
    assert observations[0]["account_key"] == "current"
    assert observations[0]["limit_id"] == "codex"
    assert observations[0]["observation_source"] == "app-server"
    assert parse_time(observations[0]["timestamp"]).tzinfo == timezone.utc
    assert parse_time("2026-09-01T08:00:00+08:00").hour == 0












def test_ssh_diagnostics_block_ready_without_losing_valid_import(tmp_path, monkeypatch):
    worker = worker_at(tmp_path, ssh_sources=[dict(id="peer", host="peer", enabled=True)])
    monkeypatch.setattr("aiquota.remote_collector.collect_ssh", lambda *_args, **_kwargs:
                        dict(records=[], observations=[], cursors={"saved": {}}, errors=[], diagnostics={"missing_baseline": 1}))
    worker._collect_remote()
    source = next(s for s in worker._store.sources() if s["id"] == "ssh:peer")
    assert source["status"] == "error"
    assert worker._store.get_meta("cursor:ssh:peer") == {"saved": {}}


@pytest.mark.parametrize("problem", [{"errors": ["OSError"]}, {"partial_files": 1}, {"diagnostics": {"malformed_lines": 1}}])
def test_incomplete_ssh_scan_keeps_valid_records_and_titles(tmp_path, monkeypatch, problem):
    worker = worker_at(tmp_path, ssh_sources=[dict(id="peer", host="peer", enabled=True)])
    result = dict(records=[record(datetime.now(timezone.utc))], observations=[], cursors={}, titles={"session": "Example task"})
    result.update(problem)
    monkeypatch.setattr("aiquota.remote_collector.collect_ssh", lambda *_args, **_kwargs: result)
    worker._collect_remote()
    assert worker._store.records()[0]["session_title"] == "Example task"
    assert worker._store.sources()[0]["status"] == "error"


def test_mock_background_worker_stops_and_never_syncs_network(tmp_path, monkeypatch):
    worker = UsageWorker(dict(default_config(), auto_sync_prices=True), mock=True, directory=tmp_path)
    ready = threading.Event()
    monkeypatch.setattr(worker, "_publish", ready.set)
    monkeypatch.setattr(PricingCatalog, "sync", lambda *_args, **_kwargs: pytest.fail("mock tried network sync"))
    try:
        worker.start()
        assert ready.wait(3)
    finally:
        assert worker.stop(3000)
    assert not worker.isRunning()


