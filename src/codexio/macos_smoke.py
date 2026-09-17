"""Opt-in, mock-only app-bundle smoke check and widget screenshots."""
from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import replace

from PySide6.QtCore import QObject, QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication

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

    def run_checks(self):
        controller = self.controller
        host = controller.dashboard_host
        popup = controller.menu_bar.preview
        assert host._data["menu_bar_today"]["tokens"] is not None
        assert host._data["latest_request"] is not None
        for theme in ("light", "dark"):
            controller.apply_config(dict(controller.config, theme=theme))
            for page in ("overview", "logs", "trends", "subscription", "pricing", "settings"):
                window = controller.open_main(page)
                yield
                assert window.isVisible() and not window._widget_toggle.isVisible()
                self.capture(window, page + "-" + theme)
                if page == "settings":
                    assert [window._settings_sections.item(i).text() for i in range(4)] == ["外观", "菜单栏", "数据来源", "应用"]
                    window._settings_sections.setCurrentRow(1)
                    yield
                    self.capture(window, "settings-menu-bar-" + theme)
            popup.show_at(controller.menu_bar.tray.geometry() if not controller.menu_bar.tray.geometry().isEmpty() else QRect(600, 0, 22, 22))
            yield
            assert popup.isVisible() and popup._timer.isActive()
            assert popup.today_tokens.text() != "—" and popup.today_cost.text().startswith("$")
            assert popup.latest_cost.text().startswith("$")
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
        assert saved.show_main_on_startup is False and popup.width() == 520
        self.checks.append("settings-roundtrip")
        controller.apply_settings(replace(saved, refresh_interval_seconds=60, menu_bar_preview_size="comfortable", show_main_on_startup=True))
        controller.close_window()
        yield
        assert host.dashboard is None and controller.usage.isRunning() and controller.worker.isRunning()
        assert not any(type(w).__name__ == "QuotaWindow" for w in QApplication.topLevelWidgets())
        self.checks.append("close-keeps-background-without-floating-widget")
        popup.show_at(QRect(600, 0, 22, 22))
        yield
        popup.open_button.click()
        yield
        assert host.dashboard is not None and host.dashboard.isVisible()
        assert not popup.isVisible()
        self.checks.append("menu-bar-reopens-main-window")


def schedule_smoke_test(controller, output):
    controller._smoke_run = SmokeRun(controller, output)
