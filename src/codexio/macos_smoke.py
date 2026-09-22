"""Opt-in, mock-only app-bundle smoke check and widget screenshots."""
from __future__ import annotations

import json
import gc
import sys
import time
import traceback
from dataclasses import replace

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QSystemTrayIcon

from codexio.settings import load_settings


class SmokeRun(QObject):
    def __init__(self, controller, output):
        super().__init__(controller)
        self.controller = controller
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.checks = []
        self.steps = None
        self._hook = sys.excepthook
        sys.excepthook = self.exception
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.advance)
        self.timer.start()

    def exception(self, kind, error, tb):
        self.finish(False, "".join(traceback.format_exception(kind, error, tb)))

    def finish(self, success, error=None):
        self.timer.stop()
        sys.excepthook = self._hook
        result = {"ok": success, "checks": self.checks, "error": error,
                  "qt_platform": QApplication.platformName()}
        (self.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.controller.stop()
        self.controller.app.exit(0 if success else 1)

    def advance(self):
        try:
            if self.steps is None:
                host = self.controller.dashboard_host
                if not host._data or not host._quota or host._loading is None or host._loading.get("loading"):
                    if time.monotonic() - self.started > 60:
                        raise AssertionError("模拟用量或额度未在 60 秒内就绪")
                    return
                self.steps = iter(self.run_checks())
            next(self.steps)
        except StopIteration:
            self.finish(True)
        except Exception:
            self.finish(False, traceback.format_exc())

    def capture(self, widget, name):
        assert widget.grab().save(str(self.output / (name + ".png")))
        self.checks.append(name)

    def drag_panel(self, handle, delta):
        origin = handle.mapToGlobal(handle.rect().center())
        target = origin + QPoint(delta, 0)
        for kind, position, button, buttons in (
            (QEvent.Type.MouseButtonPress, origin, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, target, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, target, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        ):
            QApplication.sendEvent(handle, QMouseEvent(kind, QPointF(handle.mapFromGlobal(position)), QPointF(position),
                                                       button, buttons, Qt.KeyboardModifier.NoModifier))
            QApplication.processEvents()

    def run_checks(self):
        controller = self.controller
        host = controller.dashboard_host
        popup = controller.menu_bar.preview
        # Isolated mock-only observations exercise the complete DB-to-table join.
        for index, model in enumerate(("gpt-5.6-luna", "gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-terra")):
            controller.upstream.store.record("resp_preview_%04d" % index, model)
        assert host._data["menu_bar_today"]["tokens"] is not None
        assert host._data["latest_request"] is not None
        icon = controller.app.windowIcon().pixmap(QSize(1024, 1024), 1.0)
        assert icon.width() == icon.height() == 1024, icon.size()
        self.checks.append("svg-dock-icon-retina-resolution")
        for theme in ("light", "dark"):
            controller.apply_config(dict(controller.config, theme=theme))
            for page in ("overview", "logs", "trends", "subscription", "pricing", "settings"):
                window = controller.open_main(page)
                gc.collect()
                yield
                assert window.isVisible() and not window._widget_toggle.isVisible()
                self.capture(window, page + "-" + theme)
                if page == "logs":
                    table = window._log_table
                    from codexio.desktop_widgets import UPSTREAM_ROLE
                    assert table.item(0, 1).data(UPSTREAM_ROLE) == "多上游（2）"
                    headers = [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())]
                    assert "档位" not in headers and headers[2:7] == ["输入 / 输出", "缓存命中率", "费用", "速度", "耗时"]
                    assert table.item(0, table.cache_column).text().endswith("%")
                    assert table.item(0, table.speed_column).text().endswith("Token/s")
                    window._log_mode.setCurrentIndex(window._log_mode.findData("model_call"))
                    yield
                    assert "ID " not in table.item(0, 0).text().split("\n")[-1]
                    assert table.item(0, table.speed_column).text().endswith("Token/s")
                    assert table.item(0, 1).data(UPSTREAM_ROLE) == "gpt-5.6-luna"
                    self.capture(window, "log-model-calls-" + theme)
                    window._log_mode.setCurrentIndex(window._log_mode.findData("user_request"))
                    yield
                    assert window._log_table.horizontalScrollBar().property("scrollActive") is True
                    self.drag_panel(window._sidebar_handle, -40)
                    yield
                    assert window._sidebar.width() == 140
                    self.capture(window, "sidebar-resized-" + theme)
                    window._sidebar_toggle.click()
                    yield
                    assert window._sidebar.width() == 60
                    self.capture(window, "sidebar-collapsed-" + theme)
                    window._sidebar_toggle.click()
                    yield
                    assert window._sidebar.width() == 140
                    self.drag_panel(window._sidebar_handle, -30)
                    yield
                    assert window._sidebar.width() == 60
                    self.drag_panel(window._sidebar_handle, 150)
                    yield
                    assert window._sidebar.width() == 180
                    self.drag_panel(window._log_drawer.handle, -80)
                    yield
                    assert window._log_drawer.inspector.width() == 320
                    self.capture(window, "log-preview-resized-" + theme)
                    self.drag_panel(window._log_drawer.handle, 80)
                    yield
                    assert window._log_drawer.inspector.width() == 240
                    window.resize(920, 740)
                    yield
                    fields = {field.accessibleName(): field for field in window._inspector_scroll.widget().findChildren(QLabel)
                              if field.accessibleName()}
                    for name in ("模型", "档位", "输入（含缓存）", "其中缓存读取", "输出", "Total Token"):
                        field = fields[name]
                        assert field.text() and field.width() >= 60, (name, field.geometry())
                        assert field.geometry().right() < field.parentWidget().width()
                    self.capture(window._log_drawer.inspector, "log-inspector-" + theme)
                    bar = window._inspector_scroll.verticalScrollBar()
                    assert bar.maximum() > 0 and bar.property("scrollActive") is False
                    bar.setValue(min(bar.value() + 30, bar.maximum()) if bar.value() < bar.maximum() else 0)
                    yield
                    assert bar.property("scrollActive") is True
                    self.capture(window._log_drawer.inspector, "log-scrolling-" + theme)
                    geometry = window._inspector_scroll.viewport().geometry()
                    deadline = time.monotonic() + 3.1
                    while time.monotonic() < deadline:
                        yield
                    assert bar.property("scrollActive") is False
                    assert window._log_table.horizontalScrollBar().property("scrollActive") is True
                    assert window._inspector_scroll.viewport().geometry() == geometry
                    self.capture(window._log_drawer.inspector, "log-scroll-idle-" + theme)
                    window.resize(1180, 780)
                if page == "trends":
                    assert window._activity_card.y() < window._trend_metrics_box.y() < window._trend_graph.y()
                    assert list(window._trend_metric_values) == ["usd", "tokens", "user_requests", "cache_hit_rate"]
                    assert window._trend_metric_values["cache_hit_rate"].text().endswith("%")
                    self.checks.append("usage-calendar-metrics-lines-" + theme)
                if page == "overview":
                    assert list(window._overview_comparisons) == ["usd", "tokens", "user_requests", "cache_hit_rate"]
                    assert window._overview_cache.text().endswith("%")
                    self.checks.append("overview-user-requests-cache-" + theme)
                if page == "settings":
                    assert [window._settings_sections.item(i).text() for i in range(4)] == ["外观", "菜单栏", "数据来源", "应用"]
                    window._settings_sections.setCurrentRow(3)
                    yield
                    assert window._auto_update.text() == "自动下载更新"
                    assert "预览模式不安装更新" in window._update_status.text()
                    assert not window._check_update.isEnabled()
                    assert window._upstream_toggle.text() == "上游检测" and not window._upstream_toggle.isChecked()
                    assert "预览模式" in window._upstream_status.text()
                    self.capture(window, "settings-updates-" + theme)
                    window._settings_sections.setCurrentRow(1)
                    yield
                    self.capture(window, "settings-menu-bar-" + theme)
            popup.show_at(controller.menu_bar.tray.geometry() if not controller.menu_bar.tray.geometry().isEmpty() else QRect(600, 0, 22, 22))
            yield
            assert popup.isVisible() and popup._timer.isActive()
            assert popup.today_tokens.text() != "—" and popup.today_cost.text().startswith("$")
            assert popup.latest_cost.text().startswith("$")
            assert popup.latest_message.geometry().bottom() < popup.latest_note.geometry().top()
            self.capture(popup, "menu-bar-" + theme)
            popup.settings_button.click()
            yield
            assert not popup.isVisible() and not popup._timer.isActive()
            assert host.dashboard._active_page == "settings"
        window = host.dashboard
        window._setting_widgets["refresh_interval_seconds"].setCurrentIndex(0)
        window._setting_widgets["menu_bar_preview_size"].setCurrentIndex(1)
        window._setting_widgets["show_main_on_startup"].setChecked(False)
        window._save_settings()
        yield
        saved = load_settings()
        assert saved.refresh_interval_seconds == 30 and saved.menu_bar_preview_size == "large"
        assert saved.show_main_on_startup is False and popup.width() == 293
        self.checks.append("settings-roundtrip")
        controller.apply_settings(replace(saved, refresh_interval_seconds=60, menu_bar_preview_size="comfortable", show_main_on_startup=True))
        controller.close_window()
        yield
        assert host.dashboard is None and controller.usage.isRunning() and controller.worker.isRunning()
        assert not any(type(w).__name__ == "QuotaWindow" for w in QApplication.topLevelWidgets())
        self.checks.append("close-keeps-background-without-floating-widget")
        # Activation used to schedule a main-window reopen after 150 ms and race
        # with menu-bar mouse events. Exercise activation before and after clicks.
        controller.app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        yield
        yield
        assert host.dashboard is None
        controller.menu_bar.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        yield
        assert popup.isVisible() and host.dashboard is None
        controller.menu_bar.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        controller.app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        yield
        yield
        assert not popup.isVisible() and host.dashboard is None
        controller.menu_bar.tray.activated.emit(QSystemTrayIcon.ActivationReason.Context)
        yield
        yield
        assert popup.isVisible() and host.dashboard is None
        self.checks.append("menu-bar-clicks-only-toggle-preview")
        yield
        popup.open_button.click()
        yield
        assert host.dashboard is not None and host.dashboard.isVisible()
        assert not popup.isVisible()
        self.checks.append("menu-bar-reopens-main-window")


def schedule_smoke_test(controller, output):
    controller._smoke_run = SmokeRun(controller, output)
