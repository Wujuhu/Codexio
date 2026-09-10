from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QTableView, QToolButton

from codexio.dashboard import Dashboard, HistoryAssignmentEditor
from codexio.desktop_widgets import DatePicker
from codexio.settings import AppSettings


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("page_name", ["trends", "logs"])
def test_date_fields_align_and_native_calendar_shows_all_six_weeks(app, theme, page_name):
    window = Dashboard(AppSettings(), {"theme": theme}, {})
    window.resize(920, 660)
    window.open_page(page_name)
    period = window._trend_period if page_name == "trends" else window._log_period
    period.setCurrentIndex(period.findData("custom"))
    row = window._trend_date_row if page_name == "trends" else window._date_row
    start = window._trend_start if page_name == "trends" else window._date_start
    end = window._trend_end if page_name == "trends" else window._date_end
    start.setDate(QDate(2026, 8, 15))
    app.processEvents()
    assert isinstance(start, DatePicker) and start.height() == end.height()
    assert row.layout().contentsMargins().left() == row.layout().contentsMargins().right() == 0
    assert start.y() == end.y()
    assert start.width() >= start.fontMetrics().horizontalAdvance("2026-08-15") + 42
    QTest.mouseClick(start, Qt.MouseButton.LeftButton, pos=QPoint(start.width() - 12, start.height() // 2))
    app.processEvents()
    calendar = start.calendarWidget()
    assert calendar.isVisible()
    view = calendar.findChild(QTableView)
    assert view is not None and view.model().rowCount() >= 7
    assert view.verticalScrollBar().maximum() == 0 and view.horizontalScrollBar().maximum() == 0
    last_row = view.model().rowCount() - 1
    cells = [view.model().index(last_row, column) for column in range(view.model().columnCount())]
    visible = [index for index in cells if not view.visualRect(index).isEmpty()]
    assert visible and all(view.viewport().rect().contains(view.visualRect(index)) for index in visible)
    last_day = next(index for index in visible if str(index.data()) == "31")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(last_day).center())
    app.processEvents()
    assert start.date() == QDate(2026, 8, 31)
    assert not calendar.isVisible()
    window.close()


def test_calendar_navigation_keyboard_selection_and_history_editor_share_control(app):
    window = Dashboard(AppSettings(), {"theme": "dark"}, {})
    editor = HistoryAssignmentEditor([], [], window)
    assert isinstance(editor.start, DatePicker) and isinstance(editor.end, DatePicker)
    picker = editor.start
    picker.setDate(QDate(2026, 8, 15))
    editor.show()
    app.processEvents()
    QTest.mouseClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(picker.width() - 12, picker.height() // 2))
    app.processEvents()
    calendar = picker.calendarWidget()
    next_month = calendar.findChild(QToolButton, "qt_calendar_nextmonth")
    assert next_month is not None
    QTest.mouseClick(next_month, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert calendar.monthShown() == 9
    calendar.setSelectedDate(QDate(2026, 9, 10))
    view = calendar.findChild(QTableView)
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Right)
    QTest.keyClick(view, Qt.Key.Key_Return)
    app.processEvents()
    assert picker.date() == QDate(2026, 9, 11)
    editor.close()
    window.close()


def test_legacy_subscription_history_view_state_is_safe_after_merge(app):
    window = Dashboard(AppSettings(), {}, {})
    window.restore_view_state({"subscription": {"_subscription_tabs": "history"}})
    window.open_page("subscription")
    app.processEvents()
    page = window._pages["subscription"]
    assert isinstance(page, QScrollArea)
    assert page.widget().isAncestorOf(window._quota_widgets["subscription"]["week"])
    assert page.widget().isAncestorOf(window._subscription_history)
    assert "_subscription_tabs" not in window.capture_view_state()["subscription"]
    window.close()
