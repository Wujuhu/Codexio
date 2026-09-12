from __future__ import annotations

import copy
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication

from codexio.charts import compact_number
from codexio.charts import UsageChart, bucket_records
from codexio.activity import UsageActivity
from codexio.dashboard import Dashboard, usd
from codexio.desktop_widgets import PeriodChange
from codexio.pricing import PricingCatalog
from codexio.settings import AppSettings
from codexio.usage_queries import UsageQueries, compare_usage, comparison_bounds
from codexio.usage_store import UsageStore


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def record(stamp, **extra):
    row = dict(timestamp=stamp.isoformat(), total_tokens=100, input_tokens=90, output_tokens=10,
               cost_usd=1, pricing_status="priced", quality="response", model="gpt-6-astra",
               provider="openai", service_tier="default", session_id="session", turn_id="turn")
    row.update(extra)
    return row


def mixed_records(now, period):
    start, _, previous_start, _ = comparison_bounds(period, now)
    rows = []
    for label, stamp, count in (("current", start, 2), ("previous", previous_start, 1)):
        entries = [record(stamp) for _ in range(count)] + [
            record(stamp, total_tokens=None, cost_usd=None, pricing_status="invalid"),
            record(stamp, total_tokens=50, input_tokens=40, cost_usd=None, pricing_status="unpriced"),
            record(stamp, total_tokens=20, input_tokens=10, cost_usd=999, pricing_status="unpriced"),
            record(stamp, total_tokens=30, input_tokens=20, cost_usd=.3, pricing_status="estimated", quality="cumulative_observation"),
            record(stamp, total_tokens=-1, cost_usd=-1, pricing_status="invalid", quality="invalid"),
        ]
        rows.extend(dict(row, id=f"{label}-{index}") for index, row in enumerate(entries))
    rows += [record(now, timestamp="unknown", id="undated"),
             record(now + timedelta(seconds=1), id="future"),
             record(previous_start - timedelta(seconds=1), id="too-old")]
    return rows


@pytest.mark.parametrize("period", ["today", "week", "month"])
def test_partial_records_are_filtered_per_metric_in_both_periods_without_mutation(period):
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    rows = mixed_records(now, period)
    original = copy.deepcopy(rows)
    result = compare_usage(iter(rows), period, now)
    for name, expected in (("current", (300, 2.3, 5)), ("previous", (200, 1.3, 4))):
        assert tuple(result[name][key] for key in ("tokens", "usd", "requests")) == pytest.approx(expected)
        assert result[name]["skipped"] == dict(tokens=2, usd=4, requests=2)
    assert result["changes"]["tokens"]["percent"] == 50
    assert result["changes"]["usd"]["percent"] == pytest.approx((2.3 - 1.3) / 1.3 * 100)
    assert result["changes"]["requests"]["percent"] == 25
    assert all(change["status"] == "ready" for change in result["changes"].values())
    assert rows == original


@pytest.mark.parametrize("bad", [None, -1, True, 1.5, "bad", float("nan"), float("inf")])
def test_invalid_token_counters_do_not_become_zero_or_erase_independent_metrics(bad):
    now = datetime.now().astimezone()
    result = compare_usage([record(now, total_tokens=bad), record(now - timedelta(days=1))], "today", now)
    assert result["current"]["tokens"] is None
    assert result["changes"]["tokens"] == dict(status="no_current", percent=None)
    assert result["current"]["requests"] == result["current"]["usd"] == 1
    assert result["changes"]["requests"]["percent"] == result["changes"]["usd"]["percent"] == 0


@pytest.mark.parametrize("bad", [None, -1, True, "bad", float("nan"), float("inf")])
def test_invalid_costs_do_not_erase_tokens_or_calls(bad):
    now = datetime.now().astimezone()
    result = compare_usage([record(now, cost_usd=bad), record(now - timedelta(days=1))], "today", now)
    assert result["current"]["usd"] is None
    assert result["changes"]["usd"] == dict(status="no_current", percent=None)
    assert result["changes"]["tokens"]["percent"] == result["changes"]["requests"]["percent"] == 0


@pytest.mark.parametrize("status", ["unpriced", "partial", "invalid", "unknown"])
def test_price_status_prevents_stale_numeric_amount_from_being_counted(status):
    now = datetime.now().astimezone()
    result = compare_usage([record(now), record(now, cost_usd=1000, pricing_status=status),
                            record(now - timedelta(days=1))], "today", now)
    assert result["current"]["usd"] == 1
    assert result["changes"]["usd"]["percent"] == 0
    assert result["changes"]["tokens"]["percent"] == result["changes"]["requests"]["percent"] == 100


@pytest.mark.parametrize("quality", ["aggregate", "cumulative", "cumulative_observation:invalid_subsets",
                                     "cumulative_delta", "unresolved", "invalid", "unknown"])
def test_unconfirmed_calls_are_unknown_not_a_zero_call_baseline(quality):
    now = datetime.now().astimezone()
    result = compare_usage([record(now), record(now - timedelta(days=1), quality=quality)], "today", now)
    assert result["previous"]["requests"] is None
    assert result["changes"]["requests"] == dict(status="no_history", percent=None)


def test_confirmed_response_delta_and_legacy_calls_keep_collector_identity_rules():
    now = datetime.now().astimezone()
    result = compare_usage([record(now, id="response:session:r1", quality="cumulative_delta"),
                            record(now, id="legacy:1", quality="legacy_last"),
                            record(now - timedelta(days=1))], "today", now)
    assert result["current"]["requests"] == 2
    assert result["changes"]["requests"]["percent"] == 100


def test_total_identity_is_checked_without_confusing_invalid_price_subsets_with_total():
    now = datetime.now().astimezone()
    result = compare_usage([record(now, total_tokens=999),
                            record(now, quality="response:invalid_subsets", cached_input_tokens=500,
                                   cost_usd=None, pricing_status="invalid"),
                            record(now - timedelta(days=1))], "today", now)
    assert result["current"]["tokens"] == 100
    assert result["current"]["requests"] == 2
    assert result["current"]["skipped"]["tokens"] == 1


def test_integral_numeric_strings_and_large_integers_remain_exact():
    now = datetime.now().astimezone()
    count = 9007199254740993
    rows = [record(now, total_tokens=str(count), input_tokens=str(count - 1), output_tokens="1"),
            record(now - timedelta(days=1), total_tokens=count, input_tokens=count - 1, output_tokens=1)]
    result = compare_usage(rows, "today", now)
    assert result["current"]["tokens"] == result["previous"]["tokens"] == count
    assert result["changes"]["tokens"]["percent"] == 0


@pytest.mark.parametrize("current,previous,status,percent,text", [
    (0, 0, "ready", 0, "0.0%"), (100, 0, "zero_baseline", None, "前期为 0"),
    (0, 100, "ready", -100, "−100.0%"), (None, 0, "no_current", None, "暂无对比"),
    (0, None, "no_history", None, "暂无对比"), (None, None, "no_current", None, "暂无对比"),
])
def test_zero_and_unknown_have_distinct_comparison_states(app, current, previous, status, percent, text):
    now = datetime.now().astimezone()
    rows = [record(stamp, total_tokens=count, input_tokens=count, output_tokens=0, cost_usd=count,
                   pricing_status="priced" if count is not None else "unpriced")
            for stamp, count in ((now, current), (now - timedelta(days=1), previous))]
    result = compare_usage(rows, "today", now)
    widget = PeriodChange()
    for metric in ("tokens", "usd"):
        assert result["changes"][metric] == dict(status=status, percent=percent)
        widget.set_comparison(result, metric, "light")
        assert widget.value.text() == text
        if current is None or previous is None:
            assert "暂无有效数据" in widget.toolTip()
    # Zero-token responses still confirm one independent call in each period.
    widget.set_comparison(result, "requests", "light")
    assert widget.value.text() == "0.0%"
    widget.close()


@pytest.mark.parametrize("period", ["today", "week", "month"])
def test_database_model_filter_deduplication_and_raw_history_remain_intact(tmp_path, period):
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    start, _, previous_start, _ = comparison_bounds(period, now)
    store = UsageStore(tmp_path / "usage.sqlite")
    catalog = PricingCatalog(tmp_path / "prices")
    queries = UsageQueries(store.path)
    rows = [record(start, id="a-current", response_id="r1"), record(start, id="a-current2"),
            record(previous_start, id="a-previous", response_id="r2"),
            record(start, id="unknown-price", provider="unknown"),
            record(previous_start, id="invalid-tokens", total_tokens=None),
            record(start, id="aggregate", quality="cumulative_observation"),
            record(start, id="b-current", model="gpt-5.6-terra", total_tokens=900, input_tokens=890),
            record(previous_start, id="b-previous", model="gpt-5.6-terra")]
    store.upsert_records(rows, "local")
    store.upsert_records([dict(rows[0], id="copied-current"), dict(rows[2], id="copied-previous")], "ssh:copy")
    queries.rebuild(catalog)
    with sqlite3.connect(store.path) as db:
        before = db.execute("SELECT id,data FROM usage_records ORDER BY id").fetchall()
    result = queries.period_comparison(period, now, "gpt-6-astra")
    cost = catalog.price(record(start))["usd"]
    assert result["current"]["tokens"] == 400
    assert result["previous"]["tokens"] == 100
    assert result["current"]["usd"] == pytest.approx(3 * cost)
    assert result["previous"]["usd"] == pytest.approx(cost)
    assert result["current"]["requests"] == 3
    assert result["previous"]["requests"] == 2
    assert tuple(result["changes"][m]["percent"] for m in ("tokens", "usd", "requests")) == pytest.approx((300, 200, 50))
    assert queries.period_comparison(period, now, "gpt-5.6-terra")["changes"]["tokens"]["percent"] == 800
    assert queries.period_comparison(period, now, "no-model")["current"]["tokens"] is None
    assert queries.page(mode="model_call", page_size=100)["total"] == 8
    assert queries.record("invalid-tokens")["total_tokens"] is None
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT id,data FROM usage_records ORDER BY id").fetchall() == before


@pytest.mark.parametrize("period", ["today", "week", "month"])
def test_overview_and_filtered_trend_cards_use_the_comparison_current_totals(app, period):
    now = datetime.now().astimezone()
    rows = mixed_records(now, period)
    # Future and out-of-period records remain available to the raw history.
    rows += [record(now, id="other-current", model="gpt-5.6-terra", cost_usd=50),
             record(comparison_bounds(period, now)[2], id="other-previous", model="gpt-5.6-terra", cost_usd=10)]
    window = Dashboard(AppSettings(), {"theme": "light"}, {})
    data = dict(records=rows, available_models=["gpt-6-astra", "gpt-5.6-terra"], sources_complete=False,
                summaries={period: dict(tokens=999999, usd=999999, requests=999999)})
    window.apply_data(data)
    window.open_page("overview", period)
    comparison = window._period_comparison(period)
    assert window._overview_tokens.text() == compact_number(comparison["current"]["tokens"])
    assert window._overview_cost.text() == usd(comparison["current"]["usd"])
    assert window._overview_calls.text() == format(comparison["current"]["requests"], ",")
    assert all("按已确认数据计算" in widget.toolTip() for widget in window._overview_comparisons.values())
    assert all(widget.value.text() != "数据同步中" for widget in window._overview_comparisons.values())
    window.open_page("trends", period)
    window._trend_model.setCurrentIndex(window._trend_model.findData("gpt-6-astra"))
    result = window._period_comparison(period, "gpt-6-astra")
    assert tuple(result["current"][key] for key in ("tokens", "usd", "requests")) == pytest.approx((300, 2.3, 5))
    assert window._trend_metric_values["tokens"].text() == "300"
    assert window._trend_metric_values["usd"].text() == "$2.30"
    assert window._trend_metric_values["requests"].text() == "5"
    assert tuple(window._trend_comparisons[key].value.text() for key in ("tokens", "usd", "requests")) == ("+50.0%", "+76.9%", "+25.0%")
    window.close()


def test_empty_and_unconfirmed_card_values_do_not_display_zero(app):
    now = datetime.now().astimezone()
    window = Dashboard(AppSettings(), {}, {})
    for rows in ([], [record(now, total_tokens=None, cost_usd=None, pricing_status="invalid", quality="unresolved")]):
        window.apply_data(dict(records=rows, sources_complete=False))
        window.open_page("overview", "today")
        assert all(widget.text() == "—" for widget in (window._overview_cost, window._overview_tokens, window._overview_calls))
        assert all(widget.value.text() == "暂无对比" for widget in window._overview_comparisons.values())
        window.open_page("trends", "today")
        assert all(widget.text() == "—" for widget in window._trend_metric_values.values())
        assert all(widget.value.text() == "暂无对比" for widget in window._trend_comparisons.values())
    window.close()


@pytest.mark.parametrize("bad", ["not-a-number", float("nan"), float("inf")])
def test_malformed_records_do_not_prevent_overview_or_trends_from_rendering(app, bad):
    now = datetime.now().astimezone()
    rows = [record(now), record(now - timedelta(days=1)),
            record(now, total_tokens=bad, input_tokens=bad, cost_usd=bad, pricing_status="invalid")]
    window = Dashboard(AppSettings(), {}, {})
    try:
        window.apply_data(dict(records=rows, sources_complete=False))
        for page in ("overview", "trends"):
            window.open_page(page, "today")
            app.processEvents()
            comparisons = window._overview_comparisons if page == "overview" else window._trend_comparisons
            assert comparisons["tokens"].value.text() == comparisons["usd"].value.text() == "0.0%"
            assert comparisons["requests"].value.text() == "+100.0%"
    finally:
        window.close()


def test_invalid_only_chart_bucket_keeps_token_and_price_unknown(app):
    now = datetime.now().astimezone()
    buckets = bucket_records([record(now, total_tokens="bad", input_tokens="bad", cost_usd="bad")], now=now)
    occupied = next(row for row in buckets if row["requests"])
    assert occupied["tokens"] is occupied["usd"] is None
    chart = UsageChart()
    chart.set_buckets(buckets)
    assert "Total Token  暂无有效数据" in chart._tooltip_text(occupied)
    activity = UsageActivity()
    activity.set_buckets(buckets, now)
    assert activity.summary_text() == "1 天有记录 · Token 暂无有效数据"
    assert "Total Token  暂无有效数据" in activity.tooltip_text(now.date())
    chart.close()
    activity.close()


def test_undated_history_is_not_assigned_to_either_period_or_all_time_cards(tmp_path):
    now = datetime.now().astimezone()
    store = UsageStore(tmp_path / "usage.sqlite")
    rows = [record(now, id="current"), record(now - timedelta(days=1), id="previous"), record(now, id="undated")]
    store.upsert_records(rows, "local")
    # Simulate a historical/imported record whose time cannot be interpreted.
    with sqlite3.connect(store.path) as db:
        raw = json.loads(db.execute("SELECT data FROM usage_records WHERE id='undated'").fetchone()[0])
        raw["timestamp"] = "unknown"
        db.execute("UPDATE usage_records SET timestamp='',data=? WHERE id='undated'", (json.dumps(raw),))
    queries = UsageQueries(store.path)
    queries.rebuild(PricingCatalog(tmp_path / "prices"))
    result = queries.period_comparison("today", now)
    assert result["current"]["requests"] == result["previous"]["requests"] == 1
    assert queries.confirmed_summary(now)["requests"] == 2
    assert queries.record("undated")["timestamp"] == "unknown"


@pytest.mark.parametrize("period", ["today", "week", "month", "all"])
def test_database_overview_cards_read_confirmed_totals_instead_of_worker_summaries(app, tmp_path, period):
    now = datetime.now().astimezone()
    current = now.replace(hour=0, minute=0, second=0, microsecond=0)
    previous = comparison_bounds(period if period != "all" else "today", now)[2]
    store = UsageStore(tmp_path / "usage.sqlite")
    rows = [record(current, id="good"), record(current, id="unpriced", provider="unknown"),
            record(current, id="bad", total_tokens=None), record(previous, id="old")]
    store.upsert_records(rows, "local")
    queries = UsageQueries(store.path)
    queries.rebuild(PricingCatalog(tmp_path / "prices"))
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data(dict(query_path=str(store.path), summaries={period: dict(tokens=99999, usd=999, requests=999)}, sources_complete=False))
    window.open_page("overview", period)
    comparison = window._period_comparison(period)
    totals = comparison["current"] if comparison else queries.confirmed_summary(now)
    assert window._overview_tokens.text() == compact_number(totals["tokens"])
    assert window._overview_cost.text() == usd(totals["usd"])
    assert window._overview_calls.text() == format(totals["requests"], ",")
    if period != "all":
        window.open_page("trends", period)
        assert window._trend_metric_values["tokens"].text() == compact_number(totals["tokens"])
        assert window._trend_metric_values["usd"].text() == usd(totals["usd"])
        assert window._trend_metric_values["requests"].text() == format(totals["requests"], ",")
    else:
        assert not any(widget.isVisible() for widget in window._overview_comparisons.values())
    window.close()


@pytest.mark.parametrize("database", [False, True])
def test_custom_and_all_trend_totals_follow_dates_models_and_clear_invalid_ranges(app, tmp_path, database):
    from PySide6.QtCore import QDate
    day = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=3)
    end = day.replace(hour=23, minute=59, second=59, microsecond=999999)
    rows = [record(day, id="start"), record(end, id="end"),
            record(day + timedelta(hours=1), id="unpriced", provider="unknown", cost_usd=None, pricing_status="unpriced"),
            record(day + timedelta(hours=2), id="other", model="gpt-5.6-terra"),
            record(day - timedelta(microseconds=1), id="before"),
            record(end + timedelta(microseconds=1), id="after")]
    window = Dashboard(AppSettings(), {"theme": "light"}, {})
    if database:
        store = UsageStore(tmp_path / "usage.sqlite")
        store.upsert_records(rows, "local")
        catalog = PricingCatalog(tmp_path / "prices")
        queries = UsageQueries(store.path)
        queries.rebuild(catalog)
        # Preserve exact microsecond endpoints to exercise the display index's date bounds.
        with sqlite3.connect(store.path) as db:
            for row in rows:
                stamp = datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc).isoformat(timespec="microseconds")
                db.execute("UPDATE usage_priced_calls SET timestamp=?,metrics=? WHERE id=?", (stamp, json.dumps(row), row["id"]))
        window.apply_data(dict(query_path=str(store.path), query_generation=1, available_models=["gpt-6-astra", "gpt-5.6-terra"]))
    else:
        window.apply_data(dict(records=rows, available_models=["gpt-6-astra", "gpt-5.6-terra"]))
    window.open_page("trends", "today")
    window._trend_model.setCurrentIndex(window._trend_model.findData("gpt-6-astra"))
    selected = QDate(day.year, day.month, day.day)
    window._trend_start.setDate(selected)
    window._trend_end.setDate(selected)
    window._trend_period.setCurrentIndex(window._trend_period.findData("custom"))
    assert not window._trend_metrics_box.isHidden()
    assert tuple(window._trend_metric_values[k].text() for k in ("tokens", "usd", "requests")) == ("300", "$2.00", "3")
    assert all(w.isHidden() for w in window._trend_comparisons.values())
    assert "按已确认数据计算" in window._trend_metric_values["usd"].toolTip()
    window._trend_model.setCurrentIndex(window._trend_model.findData("gpt-5.6-terra"))
    assert tuple(window._trend_metric_values[k].text() for k in ("tokens", "usd", "requests")) == ("100", "$1.00", "1")
    window._trend_model.setCurrentIndex(window._trend_model.findData("gpt-6-astra"))
    window._trend_period.setCurrentIndex(window._trend_period.findData("all"))
    assert tuple(window._trend_metric_values[k].text() for k in ("tokens", "usd", "requests")) == ("500", "$4.00", "5")
    window._trend_period.setCurrentIndex(window._trend_period.findData("custom"))
    window._trend_start.setDate(selected.addDays(1))
    assert all(w.text() == "—" for w in window._trend_metric_values.values())
    assert not window._trend_note.isHidden()
    window._trend_end.setDate(selected.addDays(2))
    window._trend_start.setDate(selected.addDays(2))
    assert all(w.text() == "—" for w in window._trend_metric_values.values())
    assert window._trend_note.isHidden()
    window.close()
