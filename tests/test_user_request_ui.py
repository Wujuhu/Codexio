from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics

from codexio.dashboard import Dashboard, USER_REQUEST_HEADERS, CALL_HEADERS, UserRequestDetails
from codexio.settings import AppSettings
from codexio.user_requests import turn_key


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def data(start=None, status="running"):
    start = start or datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    session = "11111111-1111-4111-8111-111111111111"
    records = [dict(id="call-%d" % index, response_id="r%d" % index, turn_id="t1", session_id=session,
        timestamp=(start + timedelta(minutes=index + 1)).isoformat(), model=model, service_tier=tier,
        source_id=source, source_name=source, input_tokens=100 + index, cached_input_tokens=60,
        output_tokens=10, total_tokens=110 + index, cost_usd=cost, pricing_status="priced")
        for index, (model, tier, source, cost) in enumerate([("model-a", "default", "local", 1.25), ("model-b", "priority", "ssh:server", 2.75)])]
    turn = dict(id=turn_key(session, "t1"), session_id=session, turn_id="t1", verified=True,
                started_at=start.isoformat(), started_inferred=False, status=status, prompt_preview="User prompt",
                output_preview="Final answer" if status == "completed" else "", latest_output_preview="Working",
                ended_at=(start + timedelta(minutes=3)).isoformat() if status in ("completed", "aborted") else "")
    return {"records": records, "turns": [turn]}


def test_default_groups_full_request_and_can_switch_to_calls(app):
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    window.apply_data(data())
    assert window._log_mode.currentData() == "user_request"
    assert window._log_table.rowCount() == 1
    assert [window._log_table.horizontalHeaderItem(i).text() for i in range(8)] == [
        "用户请求 / 发起时间", "模型", "档位", "输入 / 输出", "API 等价", "耗时", "状态", "来源"]
    assert window._log_table.item(0, 2).text() == "Mixed"
    assert window._log_table.item(0, 4).text().splitlines()[0] == "$4.00"
    assert window._log_table.item(0, 6).text() == "回复中"
    window._log_mode.setCurrentIndex(window._log_mode.findData("model_call"))
    assert window._log_table.rowCount() == 2
    assert [window._log_table.horizontalHeaderItem(i).text() for i in range(7)] == [
        "关联输入 / 计量时间", "模型", "档位", "输入 / 输出", "API 等价", "耗时", "来源"]
    assert not hasattr(window, "_request_filter_hint")
    window.deleteLater()


def test_source_model_filter_keeps_whole_turn_cost(app):
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    window.apply_data(data())
    window._log_source.setCurrentIndex(window._log_source.findData("ssh:server"))
    window._log_model.setCurrentIndex(window._log_model.findData("model-b"))
    assert window._log_table.rowCount() == 1 and window._log_table.item(0, 4).text().splitlines()[0] == "$4.00"
    window._log_model.setCurrentIndex(window._log_model.findData("model-a"))
    assert window._log_table.rowCount() == 0
    window.deleteLater()


def test_cross_midnight_request_belongs_to_start_date(app):
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    payload = data(midnight - timedelta(minutes=1), "completed")
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    window.apply_data(payload)
    assert window._log_table.rowCount() == 0
    window._log_period.setCurrentIndex(window._log_period.findData("all"))
    assert window._log_table.rowCount() == 1
    assert window._log_table.item(0, 4).text().splitlines()[0] == "$4.00"
    window._log_mode.setCurrentIndex(window._log_mode.findData("model_call"))
    window._log_period.setCurrentIndex(window._log_period.findData("today"))
    assert window._log_table.rowCount() == 2
    window.deleteLater()


@pytest.mark.parametrize("status,label", [("running", "回复中"), ("completed", "完成"), ("aborted", "已中断"), ("unknown", "未知")])
def test_request_status_text_and_home_share_the_same_aggregate(app, status, label):
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    payload = data(status=status)
    window.apply_data(payload)
    assert window._log_table.item(0, 6).text() == label
    window.open_page("overview")
    assert window._latest_record["cost_usd"] == 4
    assert window._latest_record["status_label"] == label
    assert window._recent_table.item(0, 4).text() == label
    assert "2 次调用" in window._recent_table.item(0, 0).text()
    window.deleteLater()


def test_completed_state_and_late_cost_update_existing_row(app):
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    payload = data()
    window.apply_data(payload)
    identity = window._filtered_records[0]["id"]
    payload = data(status="completed")
    payload["records"][1]["cost_usd"] = 3.75
    window.apply_data(payload)
    assert window._log_table.rowCount() == 1 and window._filtered_records[0]["id"] == identity
    assert window._log_table.item(0, 6).text() == "完成"
    window.open_page("overview")
    assert window._latest_record["cost_usd"] == 5
    window.deleteLater()


def test_group_details_expose_member_calls_and_short_update_label(app):
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data(data())
    window.open_page("settings")
    assert window._auto_update.text() == "自动下载更新"
    window.open_page("logs")
    window._show_request_row(0)
    dialog = window._dialogs[-1]
    assert isinstance(dialog, UserRequestDetails)
    assert dialog.call_table.rowCount() == 2
    assert dialog.call_table.columnCount() == 9
    dialog.close()
    window.deleteLater()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [920, 1190, 1600])
def test_compact_status_and_fast_columns_stay_legible(app, theme, width):
    window = Dashboard(AppSettings(), {"theme": theme, "show_log_source": True}, {})
    window.apply_data(data())
    window.resize(width, 800)
    window.open_page("logs")
    for _ in range(3):
        app.processEvents()
    table = window._log_table
    font = QFont(table.font())
    font.setPixelSize(12)
    fm = QFontMetrics(font)
    assert table.columnWidth(2) >= fm.horizontalAdvance("Standard") + 18
    assert table.columnWidth(6) >= fm.horizontalAdvance("回复中") + 18
    assert table.columnWidth(2) < table.columnWidth(1)
    assert table.columnWidth(6) < table.columnWidth(7)
    assert table.horizontalHeaderItem(7).text() == "来源"
    for column in range(table.columnCount()):
        assert table.horizontalHeaderItem(column).textAlignment() == Qt.AlignmentFlag.AlignCenter
        assert table.item(0, column).textAlignment() == Qt.AlignmentFlag.AlignCenter
    window.hide()
    window.deleteLater()


@pytest.mark.parametrize("mode", ["user_request", "model_call"])
def test_balanced_fast_gutters_and_table_fill_available_width(app, mode):
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data(data())
    window.resize(1400, 800)
    window.open_page("logs")
    window._log_mode.setCurrentIndex(window._log_mode.findData(mode))
    for _ in range(3):
        app.processEvents()
    table = window._log_table
    font = QFont(table.font())
    font.setPixelSize(12)
    fm = QFontMetrics(font)
    content_width = lambda column: max(fm.horizontalAdvance(line) for line in table.item(0, column).text().splitlines())
    for column in (3, 4, 5):
        assert table.columnWidth(column) >= content_width(column) + 18
    assert table.geometry().right() < window._log_drawer.inspector.geometry().left()
    assert abs(window._log_drawer.inspector.geometry().right() - table.parentWidget().contentsRect().right()) <= 1
    assert sum(table.columnWidth(i) for i in range(table.columnCount())) == table.viewport().width()
    assert table.columnWidth(2) < table.columnWidth(1)
    assert abs(window._log_pagination.width() - window._log_drawer.width()) <= 2
    window.hide()
    window.deleteLater()
