from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from codexio.activity import UsageActivity, activity_bounds
from codexio.charts import UsageChart, bucket_records, series_paths
from codexio.dashboard import Dashboard, PRICE_STATUS_LABELS, request_cost_text
from codexio.settings import AppSettings
from codexio.theme import theme_colors


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


def record(index=0, **extra):
    row = dict(id="call-%d" % index, session_id="session-%d" % index, turn_id="turn", model="model-a", source_id="local",
               timestamp=(datetime.now().astimezone() - timedelta(minutes=index + 1)).isoformat(), service_tier="default",
               prompt_preview="真实输入", total_tokens=100 + index, input_tokens=90 + index, output_tokens=10,
               cost_usd=1, pricing_status="priced")
    row.update(extra)
    return row


def open_preview(app):
    window = Dashboard(AppSettings(), {"theme": "dark"}, {})
    window.apply_data({"records": [record(), record(1)]})
    window.open_page("logs", "all")
    app.processEvents()
    table = window._log_table
    point = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert window._log_drawer.inspector.isVisible() and window._inspected_record is not None
    return window


@pytest.mark.parametrize("outside", ["same_row", "other_row", "blank", "search", "navigation"])
def test_details_persist_and_other_rows_switch_on_the_first_click(app, outside):
    window = open_preview(app)
    drawer = window._log_drawer
    assert not any(button.text() == "关闭" for button in drawer.inspector.findChildren(QPushButton))
    if outside in ("same_row", "other_row"):
        table = window._log_table
        row = 0 if outside == "same_row" else 1
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(table.item(row, 0)).center())
    elif outside == "blank":
        QTest.mouseClick(window._page_title, Qt.MouseButton.LeftButton)
    elif outside == "search":
        QTest.mouseClick(window._log_search, Qt.MouseButton.LeftButton)
        assert window._log_search.hasFocus()
    else:
        QTest.mouseClick(window._navigation.viewport(), Qt.MouseButton.LeftButton,
                         pos=window._navigation.visualItemRect(window._navigation.item(0)).center())
        assert window._active_page == "overview"
    assert window._inspected_record["session_id"] == ("session-1" if outside == "other_row" else "session-0")
    if outside == "navigation":
        window.open_page("logs", "all")
    assert drawer.inspector.isVisible()
    window.close()


def test_inside_preview_selection_and_escape_remain_available(app):
    window = open_preview(app)
    session = window._inspector_scroll.findChildren(QLineEdit)[-1]
    window._inspector_scroll.ensureWidgetVisible(session)
    app.processEvents()
    QTest.mouseClick(session, Qt.MouseButton.LeftButton)
    QTest.keyClick(session, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert session.selectedText() == "session-0" and window._log_drawer.inspector.isVisible()
    QTest.keyClick(session, Qt.Key.Key_Escape)
    assert window._log_drawer.inspector.isVisible() and window._log_table.hasFocus()
    assert window._inspected_record is None and window._inspector_empty.isVisible()
    window.close()


def test_native_window_mouse_delivery_selects_a_new_record_with_one_click(app):
    window = open_preview(app)
    table = window._log_table
    point = table.viewport().mapTo(window, table.visualItemRect(table.item(1, 0)).center())
    QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert window._log_drawer.inspector.isVisible() and window._inspected_record["session_id"] == "session-1"
    window.close()


def test_request_double_click_stays_in_preview_without_a_standalone_detail_dialog(app):
    window = open_preview(app)
    table = window._log_table
    point = table.viewport().mapTo(window, table.visualItemRect(table.item(0, 0)).center())
    QTest.mouseDClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert window._log_drawer.inspector.isVisible() and window._dialogs == []
    assert not any(button.text() == "完整详情" for button in window._inspector_scroll.widget().findChildren(QPushButton))
    window.close()


@pytest.mark.parametrize("width", [600, 1000])
def test_activity_calendar_preserves_daily_values_missing_days_and_future_boundary(app, width):
    now = datetime.now().astimezone()
    start, _ = activity_bounds(now)
    rows = [record(timestamp=(now - timedelta(days=1)).isoformat(), total_tokens=150, input_tokens=140, cost_usd=None),
            record(1, timestamp=(now - timedelta(days=2)).isoformat(), total_tokens=300, input_tokens=290),
            record(2, timestamp=(now + timedelta(days=1)).isoformat(), total_tokens=99999),
            record(3, timestamp=(start - timedelta(seconds=1)).isoformat(), total_tokens=99999)]
    buckets = bucket_records(rows, "all", "day", start=start, end=now)
    widget = UsageActivity()
    widget.resize(width, 200)
    widget.set_buckets(buckets, now)
    geometry = widget.cell_geometry()
    assert len(geometry) == 365 and all(start.date() <= day <= now.date() for day, _ in geometry)
    assert all(rect.width() >= 8 and rect.right() <= width for _, rect in geometry)
    assert sum(row["tokens"] for row in widget.days.values()) == 450
    assert "未定价" in widget.tooltip_text((now - timedelta(days=1)).date())
    assert "暂无记录" in widget.tooltip_text(now.date()) and "$0" not in widget.tooltip_text(now.date())
    assert widget.level((now - timedelta(days=2)).date()) > widget.level((now - timedelta(days=1)).date()) > widget.level(now.date())
    activated = []
    widget.day_clicked.connect(activated.append)
    day, rect = next((day, rect) for day, rect in geometry if day == (now - timedelta(days=1)).date())
    widget.show()
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert activated[0]["timestamp"].date() == day and activated[0]["tokens"] == 150
    widget.close()


def test_activity_query_is_bounded_cached_across_periods_and_only_runs_on_visible_trends(app, monkeypatch):
    from codexio import usage_queries
    calls = []
    class Queries:
        def __init__(self, path): pass
        def chart_buckets(self, period, granularity, **kwargs):
            calls.append((period, granularity, kwargs))
            return []
        def filters(self): return dict(models=[], sources=[])
    monkeypatch.setattr(usage_queries, "UsageQueries", Queries)
    data = dict(query_path="unused", query_generation=1, available_models=["model-a", "model-b"], prices=[])
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data(data)
    window.open_page("pricing")
    assert not calls
    window.open_page("trends")
    annual = lambda: [call for call in calls if call[2].get("start") and call[2].get("end") and (call[2]["end"] - call[2]["start"]).days >= 364]
    assert len(annual()) == 1
    window._trend_period.setCurrentIndex(window._trend_period.findData("month"))
    assert len(annual()) == 1
    window._trend_model.setCurrentIndex(window._trend_model.findData("model-b"))
    assert len(annual()) == 2 and annual()[-1][2]["model"] == "model-b"
    window.hide()
    window.apply_data(dict(data, query_generation=2))
    assert len(annual()) == 2
    window.show()
    app.processEvents()
    assert len(annual()) == 3
    window.apply_data(dict(data, query_generation=2))
    assert len(annual()) == 4  # Time can advance while the priced index stays unchanged.
    window.close()


def test_activity_drilldown_uses_call_day_and_selected_model(app):
    today = datetime.now().astimezone()
    yesterday = today - timedelta(days=1)
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data({"records": [record(timestamp=yesterday.isoformat()), record(1, model="model-b"), record(2)],
                       "available_models": ["model-a", "model-b"]})
    window.open_page("trends")
    window._trend_model.setCurrentIndex(window._trend_model.findData("model-a"))
    window._activity_chart._activate(yesterday.date())
    assert window._active_page == "logs" and window._log_mode.currentData() == "model_call"
    assert window._log_model.currentData() == "model-a" and window._log_period.currentData() == "custom"
    assert window._date_start.date().toPython() == yesterday.date() == window._date_end.date().toPython()
    assert [row["id"] for row in window._filtered_records] == ["call-0"]
    window.close()


def test_all_series_have_separate_fill_paths_and_missing_price_gaps_stay_open():
    plot = QRectF(0, 0, 400, 200)
    buckets = [dict(usd=value) for value in (1, 2, None, 3, 4)]
    line, areas = series_paths(buckets, "usd", plot, 5)
    assert len(areas) == 2
    assert areas[0].boundingRect().right() == 100 and areas[1].boundingRect().left() == 300
    assert not any(area.contains(QPoint(200, 190)) for area in areas)
    assert not line.isEmpty()
    for key, _, _ in UsageChart.SERIES:
        _, filled = series_paths([{key: 10}, {key: 20}], key, plot, 30)
        assert len(filled) == 1 and filled[0].contains(QPoint(200, 190))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_data_palette_is_distinct_and_tooltip_ink_passes_contrast(theme):
    def luminance(value):
        c = QColor(value)
        channels = [c.redF(), c.greenF(), c.blueF()]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    colors = theme_colors(theme)
    keys = ["chart_cost", "chart_tokens", "chart_input", "chart_cache_read", "chart_cache_write", "chart_output"]
    assert len(set(colors[key] for key in keys)) == 6
    for key in keys:
        first, second = sorted([luminance(colors[key + "_ink"]), luminance(colors["surface"])])
        assert (second + .05) / (first + .05) >= 4.5


def test_api_amounts_have_no_estimate_badge_without_changing_price_status():
    row = record(pricing_status="estimated")
    assert request_cost_text(row) == "$1.00" and row["pricing_status"] == "estimated"
    assert "估算" not in PRICE_STATUS_LABELS["estimated"]
    assert "部分未定价" in request_cost_text(record(pricing_status="partial"))
    assert request_cost_text(record(pricing_status="unpriced", cost_usd=None)) == "未定价"
