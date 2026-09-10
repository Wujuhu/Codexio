from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from codexio.analytics_config import default_config
from codexio.charts import bucket_records
from codexio.estimation import estimate_weeks
from codexio.pricing import PricingCatalog
from codexio.usage_queries import UsageQueries, summarize_model_calls
from codexio.usage_store import UsageStore
from codexio.usage_worker import UsageWorker, summarize
from codexio.user_requests import aggregate_user_requests, matches_call, turn_key


def record(ident, stamp="2026-09-08T01:00:00Z", **extra):
    row = dict(id=ident, session_id="parent", turn_id="t1", timestamp=stamp,
               model="gpt-6-astra", service_tier="default", source_name="Local", provider="openai",
               input_tokens=1000, cached_input_tokens=200, cache_write_input_tokens=20, output_tokens=100,
               total_tokens=1100, reasoning_output_tokens=10, limit_id="codex", quality="response",
               prompt_preview="Example user input", output_preview="Example answer")
    row.update(extra)
    return row


def turn(session="parent", ident="t1", **extra):
    row = dict(id=turn_key(session, ident), session_id=session, turn_id=ident, verified=True,
               started_at="2026-09-07T23:59:00Z", started_inferred=False, ended_at="2026-09-08T02:00:00Z",
               status="completed", prompt_preview="Example request", output_preview="Completed reply", first_turn=True)
    row.update(extra)
    return row


def setup(tmp_path):
    store = UsageStore(tmp_path / "usage.sqlite")
    catalog = PricingCatalog(tmp_path / "prices")
    queries = UsageQueries(store.path)
    return store, catalog, queries


def test_model_call_composition_combines_tiers_counts_and_prices_and_keeps_missing_values():
    rows = [dict(model="gpt-6-astra", service_tier="default", cost_usd=.1, pricing_status="priced") for _ in range(24)]
    rows.append(dict(model="gpt-6-astra", service_tier="priority", cost_usd=.6, pricing_status="priced"))
    rows += [dict(model="model-b", service_tier=None, cost_usd=2), dict(model="model-b", cost_usd=None),
             dict(model="model-c", cost_usd=None), dict(model="model-zero", cost_usd=0)]
    summary = {row["model"]: row for row in summarize_model_calls(iter(rows))}
    assert summary["gpt-6-astra"]["call_count"] == 25
    assert summary["gpt-6-astra"]["service_tier"] == "mixed"
    assert summary["gpt-6-astra"]["cost_usd"] == math.fsum([.1] * 24 + [.6])
    assert summary["model-b"]["call_count"] == 2 and summary["model-b"]["cost_usd"] == 2
    assert summary["model-b"]["pricing_status"] == "partial" and summary["model-b"]["unpriced_calls"] == 1
    assert summary["model-c"]["cost_usd"] is None and summary["model-c"]["pricing_status"] == "unpriced"
    assert summary["model-zero"]["cost_usd"] == 0 and summary["model-zero"]["pricing_status"] == "priced"
    assert summarize_model_calls([]) == []


def test_database_composition_covers_entire_request_and_parent_child_calls(tmp_path):
    store, catalog, queries = setup(tmp_path)
    store.upsert_records([record("parent-%03d" % i) for i in range(25)], "local")
    store.upsert_records([record("child-%03d" % i, session_id="child", service_tier="priority") for i in range(2)], "ssh:peer")
    store.upsert_turns([turn(), turn("child", is_subagent=True, parent_session_id="parent", parent_turn_id="t1")])
    store.upsert_agent_links([dict(id="spawn", kind="spawn", parent_session_id="parent", parent_turn_id="t1", child_session_id="child")])
    queries.rebuild(catalog)
    request = queries.page()["rows"][0]
    summary = queries.request_composition(request["id"])
    assert len(summary) == 1 and summary[0]["call_count"] == request["call_count"] == 27
    assert summary[0]["cost_usd"] == request["cost_usd"]
    assert summary[0]["service_tier"] == "mixed"
    assert queries.request_composition("missing") == []


def test_cached_generated_turn_preview_falls_back_to_the_real_call_question(tmp_path):
    store, catalog, queries = setup(tmp_path)
    store.upsert_records([record("question", prompt_preview="[$impeccable](C:/skills/impeccable/SKILL.md) 检查布局 [image 1]")], "local")
    store.upsert_turns([turn(prompt_preview="<recommended_plugins>cached context</recommended_plugins>")])
    queries.rebuild(catalog)
    request = queries.page()["rows"][0]
    assert request["prompt_preview"] == "@Impeccable 检查布局\n[image] x 1"
    assert request["call_count"] == 1 and request["total_tokens"] == 1100


def priced_reference(store, catalog):
    rows = store.records()
    links = store.record_sources()
    for row in rows:
        price = catalog.price(row)
        row.update(source_ids=links[row["id"]], cost_usd=price.get("usd"),
                   pricing_status=price.get("pricing_status", "unpriced"), pricing_reason=price.get("reason", ""),
                   price_version=price.get("price_version"))
    return rows


def all_pages(queries, mode="user_request"):
    first = queries.page(mode)
    return first["rows"] + [row for page in range(1, first["pages"]) for row in queries.page(mode, page=page)["rows"]]


def assert_groups_equal(actual, expected):
    def comparable(group):
        return {key: value for key, value in group.items() if key != "member_ids"}
    assert {r["id"]: comparable(r) for r in actual} == {r["id"]: comparable(r) for r in expected}


def test_pages_preserve_whole_request_members_start_date_and_conjunctive_filters(tmp_path):
    store, catalog, queries = setup(tmp_path)
    store.upsert_records([record("parent")], "local")
    store.upsert_records([record("child", session_id="child", model="gpt-5.6-sol", service_tier="priority")], "ssh:peer")
    store.upsert_turns([turn(), turn("child", is_subagent=True, parent_session_id="parent", parent_turn_id="t1")])
    store.upsert_agent_links([dict(id="spawn", kind="spawn", parent_session_id="parent", parent_turn_id="t1", child_session_id="child")])
    queries.rebuild(catalog)
    expected = aggregate_user_requests(priced_reference(store, catalog), store.turns(), store.agent_links())
    assert_groups_equal(queries.page()["rows"], expected)
    group = queries.page()["rows"][0]
    assert group["call_count"] == 2 and "member_ids" not in group
    assert queries.page()["counts"] == dict(requests=1, subagents=0, unassigned=0)
    assert queries.page(source="local", model="gpt-5.6-sol")["total"] == 0
    assert queries.page(source="local", tier="priority")["total"] == 0
    assert queries.page(source="ssh:peer", model="gpt-5.6-sol", tier="priority")["rows"][0]["cost_usd"] == group["cost_usd"]
    midnight = datetime(2026, 9, 8, tzinfo=timezone.utc)
    assert queries.page(start=midnight)["total"] == 0
    assert queries.page("model_call", start=midnight)["total"] == 2
    assert queries.page(end=datetime(2026, 9, 7, 23, 59, tzinfo=timezone.utc))["total"] == 1
    assert queries.request_members(group["id"])["total"] == 2
    assert queries.request(group["id"]) == group
    assert queries.request_for_record("child") == group
    assert queries.request_for_record("missing") is None
    assert queries.page(tier="mixed")["rows"] == [group]
    assert queries.page(tier="mixed", source="local", model="gpt-5.6-sol")["total"] == 0
    assert queries.page(tier="mixed", source="ssh:peer", model="gpt-5.6-sol")["rows"][0]["cost_usd"] == group["cost_usd"]
    assert queries.page("model_call", tier="mixed")["total"] == 0


def test_literal_request_search_remains_paged_and_applies_filters(tmp_path):
    store, catalog, queries = setup(tmp_path)
    store.upsert_records([record(str(i), session_id="session-" + str(i), prompt_preview="Find 100%_safe 数据") for i in range(125)], "local")
    store.upsert_records([record("other", session_id="other", prompt_preview="Find ordinary data")], "local")
    queries.rebuild(catalog)
    result = queries.page(search="100%_SAFE 数据")
    assert result["total"] == 125 and len(result["rows"]) == 100
    assert len(queries.page(search="100%_safe 数据", page=1)["rows"]) == 25
    assert queries.page(search="100%_safe 数据", source="missing")["total"] == 0
    assert queries.page("model_call", search="SESSION-124")["rows"][0]["id"] == "124"
    assert queries.page("model_call", search="' OR 1=1 --")["total"] == 0


def test_database_pagination_bounds_and_member_order(tmp_path):
    store, catalog, queries = setup(tmp_path)
    start = datetime(2026, 9, 8, tzinfo=timezone.utc)
    store.upsert_records([record(str(i), (start + timedelta(seconds=i)).isoformat()) for i in range(235)], "local")
    store.upsert_turns([turn()])
    queries.rebuild(catalog)
    first, second, third = (queries.page("model_call", page=i) for i in range(3))
    assert [len(p["rows"]) for p in (first, second, third)] == [100, 100, 35]
    assert first["rows"][0]["id"] == "234" and third["rows"][-1]["id"] == "0"
    assert queries.page("model_call", page=99)["page"] == 2
    assert queries.page("model_call", page_size=999)["pages"] == 3
    members = queries.request_members(turn_key("parent", "t1"), page=1)
    assert members["total"] == 235 and members["rows"][0]["id"] == "100"
    assert queries.record("234")["id"] == "234"
    assert queries.record("missing") is None and queries.request("missing") is None
    empty = queries.request_members("missing", page=10)
    assert empty["rows"] == [] and empty["total"] == 0 and empty["page"] == 0 and empty["pages"] == 1


def test_filtered_groups_lookup_matching_members_once_using_record_index(tmp_path, monkeypatch):
    store, catalog, queries = setup(tmp_path)
    store.upsert_records([record(str(i), session_id="session-%d" % (i // 4),
                                 model="gpt-6-astra" if i % 2 == 0 else "gpt-5.6-sol",
                                 service_tier="priority" if i % 4 == 0 else "default") for i in range(600)], "local")
    queries.rebuild(catalog)
    statements = []
    connect = sqlite3.connect
    def traced_connection(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr("codexio.usage_queries.sqlite3.connect", traced_connection)
    page = queries.page(source="local", model="gpt-6-astra", tier="priority")
    assert page["total"] == 150 and len(page["rows"]) == 100
    count_sql = next(sql for sql in statements if sql.startswith("SELECT COUNT(*)") and "usage_request_groups" in sql)
    with connect(str(store.path)) as db:
        plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + count_sql)]
    # Regression: the former correlated plan scanned the model/source call set
    # for each outer group (seconds for 5k calls). Membership must now be reached
    # from each matching call by its record index, with one uncorrelated ID set.
    assert any("LIST SUBQUERY" in step for step in plan)
    assert any("request_members_record" in step and "record_id=?" in step for step in plan)
    assert not any("usage_request_members" in step and "request_id=?" in step for step in plan)


def test_pending_unassigned_independent_child_and_latest_semantics(tmp_path):
    store, catalog, queries = setup(tmp_path)
    store.upsert_turns([turn(status="running", ended_at=""), turn("orphan", is_subagent=True),
                        turn("pending", status="running", ended_at="", started_at="2026-09-08T01:00:00Z")])
    store.upsert_records([record("standalone", session_id="unassigned", turn_id="", stamp="2026-09-08T02:00:00Z")], "local")
    queries.rebuild(catalog)
    result = queries.page()
    assert result["counts"] == dict(requests=2, subagents=1, unassigned=1)
    assert queries.latest_request()["session_id"] == "pending"
    unassigned = next(r for r in result["rows"] if r["record_kind"] == "unassigned")
    assert unassigned["member_ids"] == ["standalone"]
    assert len(queries.page(source="local")["rows"]) == 4
    assert queries.page(source="local", model="gpt-6-astra")["total"] == 1


def test_unmetered_turn_copied_across_sources_preserves_all_source_memberships(tmp_path):
    store, catalog, queries = setup(tmp_path)
    pending = turn(status="running", ended_at="", output_preview="")
    store.upsert_turns([pending], "local")
    store.upsert_turns([pending], "ssh:peer")
    expected = aggregate_user_requests([], store.turns())
    queries.rebuild(catalog)
    assert_groups_equal(queries.page()["rows"], expected)
    assert queries.page()["rows"][0]["source_ids"] == ["local", "ssh:peer"]
    assert queries.page(source="local")["total"] == 1
    assert queries.page(source="ssh:peer")["total"] == 1
    assert queries.page(source="local", model="gpt-6-astra")["total"] == 0


def test_streamed_summaries_and_charts_equal_full_history_reference(tmp_path):
    store, catalog, queries = setup(tmp_path)
    now = datetime.now().astimezone().replace(microsecond=0)
    records = [record("today", (now - timedelta(hours=1)).isoformat()),
               record("old", (now - timedelta(days=70)).isoformat(), total_tokens=9007199254740993),
               record("unpriced", (now - timedelta(days=4)).isoformat(), model="unknown-model"),
               record("aggregate", (now - timedelta(days=15)).isoformat(), quality="cumulative_observation"),
               record("future", (now + timedelta(days=1)).isoformat())]
    store.upsert_records(records, "local")
    queries.rebuild(catalog)
    reference = priced_reference(store, catalog)
    assert queries.summaries(now) == summarize(reference, now)
    for period, granularity in (("today", "hour"), ("week", "day"), ("month", "day"), ("all", "week")):
        assert queries.chart_buckets(period, granularity, end=now) == bucket_records(reference, period, granularity, now=now, end=now)
    filtered = [r for r in reference if r["model"] == "gpt-6-astra"]
    assert queries.chart_buckets("all", "day", model="gpt-6-astra", end=now) == bucket_records(filtered, "all", "day", now=now, end=now)


def test_scoped_comparison_matches_record_reference_and_applies_model_filter(tmp_path):
    from codexio.usage_queries import compare_usage
    store, catalog, queries = setup(tmp_path)
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    rows = [record("current", now.isoformat()), record("previous", (now - timedelta(days=1)).isoformat()),
            record("other", now.isoformat(), model="gpt-5.6-terra"),
            record("outside", (now - timedelta(days=70)).isoformat())]
    store.upsert_records(rows, "local")
    queries.rebuild(catalog)
    reference = priced_reference(store, catalog)
    for period in ("today", "week", "month"):
        assert queries.period_comparison(period, now) == compare_usage(reference, period, now)
    selected = [row for row in reference if row["model"] == "gpt-6-astra"]
    assert queries.period_comparison("today", now, "gpt-6-astra") == compare_usage(selected, "today", now)
    assert queries.period_comparison("all", now) is None


def test_streamed_summary_preserves_integral_float_and_numeric_string_counters(tmp_path):
    store, catalog, queries = setup(tmp_path)
    now = datetime.now(timezone.utc)
    store.upsert_records([record("mixed-counts", (now - timedelta(minutes=1)).isoformat(),
                                 input_tokens=1000.0, cached_input_tokens="200", cache_write_input_tokens=0,
                                 output_tokens=100, total_tokens="1100")], "local")
    queries.rebuild(catalog)
    actual = queries.summaries(now)
    assert actual == summarize(priced_reference(store, catalog), now)
    assert actual["all"]["tokens"] == 1100
    assert actual["all"]["input_tokens"] == 1000
    assert actual["all"]["cached_input_tokens"] == 200
    assert actual["all"]["cache_hit_rate"] == .2
    assert queries.record("mixed-counts")["pricing_status"] == "priced"


@pytest.mark.parametrize("value", [True, False, -1, 0, 9007199254740993, 1000.0, 1000.5, "200", "1e3", "bad", None,
                                  float("nan"), float("inf")])
def test_streamed_counter_boundary_matches_existing_summary(value):
    from codexio.usage_queries import _count as query_count
    from codexio.usage_worker import _count as summary_count
    assert query_count(value) == summary_count(value)


def test_rebuild_revisions_cover_late_metadata_price_titles_sources_and_removals(tmp_path):
    store, catalog, queries = setup(tmp_path)
    store.upsert_records([record("one")], "local")
    store.upsert_turns([turn(status="running", ended_at="", output_preview="", latest_output_preview="Working")])
    generation = queries.rebuild(catalog)
    assert queries.rebuild(catalog) == generation
    assert UsageQueries(store.path).rebuild(catalog) == generation
    assert queries.request(turn_key("parent", "t1"))["output_preview"] == "Working"
    store.upsert_turns([turn(observed_at="2026-09-08T03:00:00Z")])
    assert queries.rebuild(catalog) == generation + 1
    assert queries.request(turn_key("parent", "t1"))["status_label"] == "完成"
    assert queries.request(turn_key("parent", "t1"))["output_preview"] == "Completed reply"
    old_cost = queries.record("one")["cost_usd"]
    catalog.set_override("gpt-6-astra", dict(input=1, cache_read=.1, cache_write=2, output=4))
    queries.rebuild(catalog)
    assert queries.record("one")["cost_usd"] != old_cost
    store.update_session_titles({"parent": "Updated task title"})
    queries.rebuild(catalog)
    assert queries.latest_request()["session_title"] == "Updated task title"
    store.upsert_records([record("one")], "ssh:archived")
    queries.rebuild(catalog)
    assert queries.page(source="ssh:archived")["total"] == 1
    assert {row["id"] for row in queries.filters()["sources"]} == {"local", "ssh:archived"}
    store.delete_source_records(["one"], "local")
    queries.rebuild(catalog)
    assert queries.page(source="local", model="gpt-6-astra")["total"] == 0
    assert queries.page(source="ssh:archived", model="gpt-6-astra")["total"] == 1
    store.clear_index()
    queries.rebuild(catalog)
    assert queries.page()["total"] == 0 and queries.page("model_call")["total"] == 0


def test_failed_rebuild_keeps_previous_calls_groups_and_source_membership_atomic(tmp_path, monkeypatch):
    store, catalog, queries = setup(tmp_path)
    store.upsert_records([record("one")], "local")
    first_generation = queries.rebuild(catalog)
    previous = queries.page()
    store.upsert_records([record("two")], "ssh:peer")
    real_price = catalog.price
    monkeypatch.setattr(catalog, "price", lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError):
        queries.rebuild(catalog)
    assert queries.page() == previous
    assert queries.page(source="ssh:peer")["total"] == 0
    monkeypatch.setattr(catalog, "price", real_price)
    assert queries.rebuild(catalog) == first_generation + 1
    assert queries.page("model_call")["total"] == 2


def test_worker_publishes_only_query_metadata_and_reuses_unchanged_materialization(tmp_path, monkeypatch):
    settings = dict(default_config(), codex_roots=[], auto_sync_prices=False,
                    account_since="2026-01-01T00:00:00Z")
    worker = UsageWorker(settings, mock=True, directory=tmp_path)
    worker._store, worker._catalog, queries = setup(tmp_path)
    worker._seed_mock()
    for name in ("records", "record_sources", "turns", "agent_links", "observations"):
        monkeypatch.setattr(worker._store, name, lambda *_args, **_kwargs: pytest.fail("full history reader used"))
    calls = []
    real_price = worker._catalog.price
    def price(row):
        calls.append(row["id"])
        return real_price(row)
    monkeypatch.setattr(worker._catalog, "price", price)
    snapshots = []
    worker.data_changed.connect(snapshots.append)
    worker._publish()
    assert len(calls) == 420
    worker._publish()
    assert len(calls) == 420
    assert snapshots[0]["query_generation"] == snapshots[1]["query_generation"]
    assert "records" not in snapshots[0] and "user_requests" not in snapshots[0]
    assert queries.page("model_call")["total"] == 420
    assert all("price_rates" not in row for row in queries.page("model_call")["rows"])
    worker._store.set_source_status("local", status="ok", name="Local changed")
    worker._publish()
    assert len(calls) == 840


def test_store_revisions_ignore_checkpoints_and_unchanged_rows(tmp_path):
    store, catalog, queries = setup(tmp_path)
    initial = store.revisions()
    store.upsert_records([record("one")], "local")
    inserted = store.revisions()
    assert inserted["ledger"] > initial["ledger"] and inserted["observations"] == initial["observations"]
    store.upsert_records([record("one")], "local")
    store.set_source_status("local", status="ok", last_scan_at="2026-09-08T01:00:00Z")
    store.set_cursor("cursor", {"offset": 100})
    assert store.revisions() == inserted
    store.upsert_observations([dict(id="quota", timestamp="2026-09-08T01:00:00Z", used_percent=10, window_minutes=10080)], "local")
    observed = store.revisions()
    assert observed["ledger"] == inserted["ledger"] and observed["observations"] > inserted["observations"]
    assert UsageStore(store.path).revisions() == observed
    store.upsert_records([record("one")], "ssh:other")
    assert store.revisions()["ledger"] > observed["ledger"]
