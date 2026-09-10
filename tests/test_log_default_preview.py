"""Page-one defaults must work with delayed data without stealing selection."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from codexio.dashboard import Dashboard
from codexio.pricing import PricingCatalog
from codexio.settings import AppSettings
from codexio.usage_queries import UsageQueries
from codexio.usage_store import UsageStore


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


def records(count=3):
    now = datetime.now().astimezone()
    return [dict(id=f"call-{i}", session_id=f"session-{i}", turn_id="turn", session_title=f"请求 {i}",
                 timestamp=(now - timedelta(seconds=i)).isoformat(), model="gpt-6-astra", provider="openai",
                 service_tier="default", quality="response", input_tokens=100, output_tokens=10, total_tokens=110,
                 cost_usd=1, pricing_status="priced", prompt_preview=f"检查第 {i} 条用量记录。") for i in range(count)]


@pytest.fixture(params=["memory", "sqlite"])
def payload(request, tmp_path):
    if request.param == "memory":
        return lambda rows: dict(records=rows)
    store = UsageStore(tmp_path / "usage.sqlite")
    queries = UsageQueries(store.path)
    catalog = PricingCatalog(tmp_path / "prices")
    def publish(rows):
        store.clear_index()
        store.upsert_records(rows, "local")
        queries.rebuild(catalog)
        return dict(query_path=str(store.path), filters=queries.filters())
    return publish


@pytest.mark.parametrize("mode", ["user_request", "model_call"])
def test_page_one_previews_first_record_when_data_arrives_after_open(app, payload, mode):
    window = Dashboard(AppSettings(), {}, {})
    try:
        window.open_page("logs")
        window._log_mode.setCurrentIndex(window._log_mode.findData(mode))
        assert window._inspected_record is None and window._log_table.currentRow() == -1
        window._log_search.setFocus()
        window.apply_data(payload(records()))
        app.processEvents()
        assert window._page_number == 0 and window._log_table.currentRow() == 0
        assert window._inspector_origin == window._rendered_log_rows[0]["id"]
        assert window._inspector_stack.currentWidget() is window._inspector_scroll
        assert window._log_search.hasFocus()
    finally:
        window.close()


def test_today_without_records_does_not_preview_yesterday_or_future(app, payload):
    now = datetime.now().astimezone()
    rows = records(2)
    rows[0]["timestamp"] = (now - timedelta(days=1)).isoformat()
    rows[1]["timestamp"] = (now + timedelta(days=1)).isoformat()
    window = Dashboard(AppSettings(), {}, {})
    try:
        window.apply_data(payload(rows))
        window.open_page("logs")
        assert window._log_period.currentData() == "today"
        assert window._log_table.rowCount() == 0
        assert window._inspected_record is None and window._log_table.currentRow() == -1
        assert window._inspector_stack.currentWidget() is window._inspector_empty
        assert not window._escape_inspector.isEnabled()
    finally:
        window.close()


def test_refresh_preserves_selected_record_and_explicit_dismissal(app, payload):
    rows = records()
    window = Dashboard(AppSettings(), {}, {})
    try:
        window.apply_data(payload(rows))
        window.open_page("logs")
        window._log_table.selectRow(1)
        window._inspect_log_row(1)
        selected = window._inspector_origin
        newer = dict(rows[0], id="newest", session_id="newest-session", timestamp=datetime.now().astimezone().isoformat())
        window.apply_data(payload([newer] + rows))
        assert window._inspector_origin == selected
        assert window._rendered_log_rows[window._log_table.currentRow()]["id"] == selected
        window.activateWindow()
        window._log_table.setFocus()
        app.processEvents()
        QTest.keyClick(window._log_table, Qt.Key.Key_Escape)
        assert window._inspected_record is None
        window.apply_data(payload([newer] + rows))
        assert window._inspected_record is None and window._log_table.currentRow() == -1
        changed = [dict(row, output_preview="已更新回复") for row in [newer] + rows]
        window.apply_data(payload(changed))
        assert window._inspected_record is None
        window.open_page("overview")
        window.open_page("logs")
        assert window._inspected_record is None
    finally:
        window.close()


def test_returning_to_first_page_restores_default_without_auto_opening_later_pages(app, payload):
    window = Dashboard(AppSettings(), {}, {})
    try:
        window.apply_data(payload(records(101)))
        window.open_page("logs")
        first = window._inspector_origin
        assert first == window._rendered_log_rows[0]["id"]
        window._change_page(1)
        assert window._page_number == 1
        assert window._inspected_record is None
        window._change_page(-1)
        assert window._page_number == 0 and window._log_table.currentRow() == 0
        assert window._inspector_origin == first
    finally:
        window.close()


def test_filter_restarts_default_even_when_rows_are_unchanged_and_empty_search_stays_closed(app, payload):
    window = Dashboard(AppSettings(), {}, {})
    try:
        window.apply_data(payload(records()))
        window.open_page("logs")
        window._close_inspector()
        window._log_search.setFocus()
        window._log_search.setText("检查第")
        QTest.qWait(260)
        assert window._inspector_origin == window._rendered_log_rows[0]["id"]
        assert window._log_search.hasFocus()
        window._log_search.setText("没有匹配结果")
        QTest.qWait(260)
        assert window._log_table.rowCount() == 0 and window._inspected_record is None
    finally:
        window.close()


@pytest.mark.parametrize("dismiss", [False, True])
def test_recreated_dashboard_respects_saved_selection_or_dismissal(app, payload, dismiss):
    data = payload(records())
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data(data)
    window.open_page("logs")
    window._log_table.selectRow(1)
    window._inspect_log_row(1)
    selected = window._inspector_origin
    if dismiss:
        window._close_inspector()
    state = window.capture_view_state()
    window.close()
    restored = Dashboard(AppSettings(), {}, {})
    try:
        restored.restore_view_state(state)
        restored.apply_data(data)
        restored.open_page("logs")
        if dismiss:
            assert restored._inspected_record is None and restored._log_table.currentRow() == -1
        else:
            assert restored._inspector_origin == selected
            assert restored._log_table.currentRow() == 1
    finally:
        restored.close()
