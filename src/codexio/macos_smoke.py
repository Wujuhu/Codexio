"""The fixed, minimal mock-only application smoke check. Do not expand it."""
from __future__ import annotations

import json
import os
import sys
import time

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication


class SmokeRun(QObject):
    def __init__(self, controller, output):
        super().__init__(controller)
        self.controller = controller
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.checks = []
        self.step = 0
        self._hook = sys.excepthook
        sys.excepthook = lambda kind, error, tb: self.finish(False, str(error))
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.advance)
        self.timer.start()

    def finish(self, success, error=None):
        self.timer.stop()
        sys.excepthook = self._hook
        result = dict(ok=success, checks=self.checks, error=error, qt_platform=QApplication.platformName())
        (self.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.controller.stop()
        self.controller.app.exit(0 if success else 1)

    def advance(self):
        try:
            host = self.controller.dashboard_host
            if self.step == 0:
                if not host._data or not host._quota or host._loading is None or host._loading.get("loading"):
                    if time.monotonic() - self.started > 30:
                        raise RuntimeError("程序未在 30 秒内完成模拟启动")
                    return
                self.window = self.controller.open_main()
                assert self.window.isVisible(), "主窗口未显示"
                self.checks.append("程序启动")
            elif self.step == 1:
                assert self.window._overview_tokens.text() != "—", "基本用量未显示"
                assert host._data["menu_bar_today"]["tokens"] is not None, "基本用量未读取"
                self.checks.append("基本数据显示")
                # One optional screenshot for a current UI edit, never a matrix.
                if os.environ.get("CODEXIO_CAPTURE_SETTINGS") == "1":
                    self.window.open_page("settings")
                    self.window._settings_sections.setCurrentRow(self.window._settings_sections.count() - 1)
                elif os.environ.get("CODEXIO_CAPTURE_PAGE") in ("subscription", "pricing"):
                    self.window.open_page(os.environ["CODEXIO_CAPTURE_PAGE"])
            elif self.step == 2:
                if os.environ.get("CODEXIO_CAPTURE_SETTINGS") == "1":
                    self.window.grab().save(str(self.output / "settings.png"))
                elif os.environ.get("CODEXIO_CAPTURE_PAGE") in ("subscription", "pricing"):
                    self.window.grab().save(str(self.output / (os.environ["CODEXIO_CAPTURE_PAGE"] + ".png")))
                self.window.close()
            elif self.step == 3:
                assert host.dashboard is None, "主窗口未正常关闭"
                self.controller.app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
            else:
                assert host.dashboard is not None and host.dashboard.isVisible(), "主窗口未能重新打开"
                self.checks.append("主窗口关闭与重开")
                self.finish(True)
                return
            self.step += 1
        except Exception as error:
            self.finish(False, str(error))


def schedule_smoke_test(controller, output):
    controller._smoke_run = SmokeRun(controller, output)
