from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from codexio.dashboard import Dashboard, request_cost_text
from codexio.desktop_widgets import LedgerTable
from codexio.pricing import PricingCatalog
from codexio.settings import AppSettings
from codexio.usage_metrics import CacheUsage, cache_hit_rate, output_speed_text
from codexio.usage_queries import UsageQueries
from codexio.usage_store import UsageStore
from codexio.user_requests import aggregate_user_requests, turn_key


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def sample():
    stamp = datetime.now().astimezone() - timedelta(seconds=10)
    def call(ident, session, turn, inp, cached, out, model="gpt-6-astra"):
        return dict(id=ident, response_id=ident, session_id=session, turn_id=turn, timestamp=stamp.isoformat(),
                    model=model, provider="openai", service_tier="default", quality="response", source_id="local",
                    input_tokens=inp, cached_input_tokens=cached, output_tokens=out, total_tokens=inp + out,
                    duration_ms=10000, cost_usd=.1, pricing_status="priced", prompt_preview="检查缓存与速度")
    rows = [call("a", "parent", "p", 100, 50, 240), call("b", "parent", "p", 900, 900, 10),
            call("c", "child", "c", 100, 10, 5, "gpt-5.6-luna"), call("d", "second", "s", 100, 20, 10),
            call("unassigned", "", "", 100, 0, 0, "gpt-5.6-sol")]
    def turn(session, ident, **extra):
        return dict(id=turn_key(session, ident), session_id=session, turn_id=ident, verified=True,
                    started_at=(stamp - timedelta(seconds=10)).isoformat(), ended_at=stamp.isoformat(),
                    started_inferred=False, status="completed", duration_ms=10000, prompt_preview="用户问题", **extra)
    turns = [turn("parent", "p"), turn("child", "c", is_subagent=True, parent_session_id="parent", parent_turn_id="p"),
             turn("second", "s"), turn("unmetered", "u")]
    links = [dict(id="spawn", kind="spawn", parent_session_id="parent", parent_turn_id="p", child_session_id="child")]
    return rows, turns, links


def test_cache_rates_are_weighted_and_invalid_components_remain_unknown():
    cache = CacheUsage()
    for row in [dict(input_tokens=100, cached_input_tokens=50), dict(input_tokens=900, cached_input_tokens=900)]:
        cache.add(row)
    assert cache.summary()["cache_hit_rate"] == .95  # Not the unweighted 75%.
    for row in [{}, dict(input_tokens=0, cached_input_tokens=0), dict(input_tokens=10, cached_input_tokens=11),
                dict(input_tokens=10, cached_input_tokens=-1), dict(input_tokens=10, cached_input_tokens=True)]:
        assert cache_hit_rate(row) is None
    assert cache_hit_rate(dict(input_tokens=100, cached_input_tokens=0)) == 0


@pytest.mark.parametrize("row,expected", [
    ({"output_tokens": 240, "duration_ms": 10000}, "24 Token/s"),
    ({"output_tokens": 0, "duration_ms": 10000}, "0 Token/s"),
    ({"output_tokens": 1, "duration_ms": 100000}, "0 Token/s"),
    ({"output_tokens": 245, "duration_ms": 10000}, "25 Token/s"),
    ({"output_tokens": 244, "duration_ms": 10000}, "24 Token/s"),
    ({"output_tokens": 240}, "—"),
    ({"output_tokens": 240, "duration_ms": 0}, "—"),
    ({"output_tokens": 240, "duration_ms": -1}, "—"),
    ({"duration_ms": 10000}, "—"),
    ({"output_tokens": True, "duration_ms": 10000}, "—"),
    ({"output_tokens": 0, "duration_ms": 10000, "record_kind": "user_request", "call_count": 0}, "—"),
])
def test_output_speed_never_invents_missing_call_timing(row, expected):
    assert output_speed_text(row) == expected


@pytest.mark.parametrize("grouped", [True, False])
@pytest.mark.parametrize("tier,suffix", [("priority", " · Fast"), ("default", ""), (None, "")])
def test_subtitle_replaces_tier_column_and_call_ids(app, grouped, tier, suffix):
    row = dict(timestamp=datetime(2026, 9, 2, 17, 0).astimezone().isoformat(), id="identifier-to-hide",
               model="gpt-6-astra", service_tier=tier, prompt_preview="问题", call_count=1,
               input_tokens=100, cached_input_tokens=80, output_tokens=240, duration_ms=10000,
               cost_usd=.1, pricing_status="priced", record_kind="user_request" if grouped else "model_call")
    table = LedgerTable(grouped=grouped)
    table.set_records([row], request_cost_text)
    expected = "9.2 17:00" + (" · 1 次调用" if grouped else "") + suffix
    assert table.item(0, 0).text() == "问题\n" + expected
    assert "档位" not in [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
    assert table.item(0, table.cache_column).text() == "80.0%"
    assert table.item(0, table.speed_column).text() == "24 Token/s"
    table.close()


@pytest.mark.parametrize("database", [False, True])
def test_cards_count_user_requests_not_calls_and_include_weighted_cache(app, tmp_path, database):
    rows, turns, links = sample()
    groups = aggregate_user_requests(rows, turns, links)
    parent = next(g for g in groups if g["session_id"] == "parent")
    assert parent["call_count"] == 3 and cache_hit_rate(parent) == pytest.approx(960 / 1100)
    assert output_speed_text(parent) == "26 Token/s"
    window = Dashboard(AppSettings(), {}, {})
    payload = dict(records=rows, turns=turns, agent_links=links,
                   available_models=["gpt-6-astra", "gpt-5.6-luna", "gpt-5.6-sol"])
    if database:
        store = UsageStore(tmp_path / "usage.sqlite")
        store.upsert_records(rows, "local")
        store.upsert_turns(turns, "local")
        store.upsert_agent_links(links)
        queries = UsageQueries(store.path)
        queries.rebuild(PricingCatalog(tmp_path / "prices"))
        payload = dict(query_path=str(store.path), available_models=payload["available_models"])
        assert queries.dashboard_summary()["requests"] == 5
        assert queries.user_request_count() == 3
    window.apply_data(payload)
    window.open_page("overview", "all")
    assert window._overview_requests.text() == "3"
    assert window._overview_cache.text() == "75.4%"
    window.open_page("trends", "all")
    assert window._trend_metric_values["user_requests"].text() == "3"
    assert window._trend_metric_values["cache_hit_rate"].text() == "75.4%"
    window._trend_model.setCurrentIndex(window._trend_model.findData("gpt-6-astra"))
    assert window._trend_metric_values["user_requests"].text() == "2"
    assert window._trend_metric_values["cache_hit_rate"].text() == "88.2%"
    window.close()


def test_request_counts_follow_start_date_while_tokens_follow_call_date(tmp_path):
    rows, turns, links = sample()
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    for row in rows:
        row["timestamp"] = (midnight + timedelta(minutes=1)).isoformat()
    for turn in turns:
        turn["started_at"] = (midnight - timedelta(minutes=1)).isoformat()
        turn["ended_at"] = (midnight + timedelta(minutes=2)).isoformat()
    store = UsageStore(tmp_path / "usage.sqlite")
    store.upsert_records(rows, "local")
    store.upsert_turns(turns, "local")
    store.upsert_agent_links(links)
    queries = UsageQueries(store.path)
    queries.rebuild(PricingCatalog(tmp_path / "prices"))
    result = queries.dashboard_summary(start=midnight, end=midnight + timedelta(hours=1))
    assert result["user_requests"] == 0 and result["requests"] == 5
    assert result["tokens"] == 1565
