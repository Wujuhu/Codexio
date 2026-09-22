from __future__ import annotations

from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from codexio.analytics_config import default_config, load_analytics_config, save_analytics_config
from codexio.dashboard import Dashboard
from codexio.settings import AppSettings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=["windows", "macos"])
def window(app, request):
    changes = []
    window = Dashboard(AppSettings(), default_config(), {"config": changes.append}, desktop_platform=request.param)
    window._test_changes = changes
    stamp = datetime.now(timezone.utc).isoformat()
    rows = [dict(id=str(i), session_id=str(i), timestamp=stamp, model="gpt-6-astra", service_tier="default",
                 input_tokens=1234, output_tokens=56, total_tokens=1290, cost_usd=0.04,
                 session_title="可调整的请求预览", prompt_preview="检查导航栏与日志预览的拖动行为。") for i in range(30)]
    window.apply_data({"records": rows})
    window.resize(1180, 780)
    window.open_page("logs")
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    app.processEvents()


def drag(app, handle, dx):
    origin = handle.mapToGlobal(handle.rect().center())
    target = origin + QPoint(dx, 0)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(handle.mapFromGlobal(target)), QPointF(target),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(handle, event)
    app.processEvents()
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(target))
    app.processEvents()


def test_sidebar_drag_collapse_toggle_and_maximum_are_saved(app, window, tmp_path):
    assert window._sidebar.width() == 180
    drag(app, window._sidebar_handle, -40)
    assert window._sidebar.width() == 140
    window._sidebar_toggle.click()
    app.processEvents()
    assert window._sidebar.width() == 60
    assert all(window._navigation.item(i).text() == "" for i in range(window._navigation.count()))
    assert window._navigation.item(1).data(Qt.ItemDataRole.AccessibleTextRole) == "日志"
    window._sidebar_toggle.click()
    assert window._sidebar.width() == 140
    drag(app, window._sidebar_handle, -30)
    assert window._sidebar.width() == 60 and window._config["sidebar_width"] == 140
    drag(app, window._sidebar_handle, 90)
    assert window._sidebar.width() == 150
    drag(app, window._sidebar_handle, 500)
    assert window._sidebar.width() == 180
    window._sidebar_toggle.click()
    path = tmp_path / "settings.json"
    save_analytics_config(window._test_changes[-1], path)
    saved = load_analytics_config(path)
    assert saved["sidebar_collapsed"] and saved["sidebar_width"] == 180
    restored = Dashboard(AppSettings(), saved, {})
    assert restored._sidebar.width() == 60
    restored.close()


def test_inspector_drag_persists_through_resize_clear_and_theme(app, window):
    host = window._log_drawer
    assert host.inspector.width() == 240
    assert host.inspector.x() - (host.primary.x() + host.primary.width()) == 6
    drag(app, host.handle, -80)
    assert host.inspector.width() == 320
    assert window._test_changes[-1]["log_preview_width"] == 320
    window._close_inspector()
    window.resize(1500, 800)
    app.processEvents()
    assert host.inspector.width() == 320
    window.config_updated(dict(window._config, theme="dark"))
    app.processEvents()
    assert host.inspector.width() == 320
    drag(app, host.handle, 800)
    assert host.inspector.width() == 220
    host.handle.setFocus()
    QTest.keyClick(host.handle, Qt.Key.Key_Left)
    assert host.inspector.width() == 228


def test_log_horizontal_indicator_is_persistent_but_vertical_still_times_out(app, window):
    table = window._log_table
    horizontal, vertical = table.horizontalScrollBar(), table.verticalScrollBar()
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert horizontal.isVisible() and horizontal.property("scrollActive") is True
    assert vertical.maximum() > 0
    geometry = table.viewport().geometry()
    vertical.setValue(20)
    assert vertical.property("scrollActive") is True
    QTest.qWait(3100)
    assert vertical.property("scrollActive") is False
    assert horizontal.property("scrollActive") is True
    assert table.viewport().geometry() == geometry


def test_usage_default_order_is_calendar_metrics_then_lines(app, window):
    window.open_page("trends")
    app.processEvents()
    assert window._activity_card.y() < window._trend_metrics_box.y() < window._trend_graph.y()
    assert window._trend_period.currentData() == "today"


def test_layout_preferences_reject_invalid_values(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"sidebar_width":999,"sidebar_collapsed":"yes","log_preview_width":null}', encoding="utf-8")
    config = load_analytics_config(path)
    assert config["sidebar_width"] == 180 and config["sidebar_collapsed"] is False
    assert config["log_preview_width"] == 240
