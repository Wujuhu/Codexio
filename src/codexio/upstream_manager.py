"""Shared lifecycle: apply routing first, then offer restart now or later."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, QEvent, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from codexio.logging_setup import get_logger
from codexio.upstream_config import UpstreamError
from codexio.upstream_service import UpstreamService
from codexio.upstream_store import UpstreamStore


class UpstreamManager(QObject):
    status_changed = Signal(str, bool, bool)
    observations_changed = Signal()
    startup_ready = Signal(bool)
    _done = Signal(object)

    def __init__(self, app, directory, config, save, parent_window, *, mock=False, service=None):
        super().__init__(app)
        self.app, self.config, self.save, self.parent_window = app, config, save, parent_window
        self.mock = mock
        self.service = service or UpstreamService(directory)
        self.store = UpstreamStore(directory / "upstream.sqlite")
        self.active = self.busy = self.closing = self.allow_quit = self.system_exit = False
        self.on_quit = None
        self._revision = 0
        self._polling = False
        self._pending_quit = None
        self._done.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._poll)
        self.app.installEventFilter(self)
        self.app.commitDataRequest.connect(self._system_quit)

    def _persist(self, **values):
        self.save(dict(self.config(), **values))

    def _status(self, message):
        self.status_changed.emit(message, self.active, self.busy)

    def _offer_restart(self, operation, after=None):
        if not self.service.clients_running():
            self._finish_action(operation, after)
            return
        self.busy = True
        self._status("已应用设置 · 等待选择重启方式" if self.active else "已恢复配置 · 等待选择重启方式")
        box = QMessageBox(self.parent_window())
        box.setWindowTitle("上游检测")
        if operation in ("enable", "startup"):
            box.setText("Codexio 已应用上游检测设置")
            box.setInformativeText("重启 ChatGPT 后应用改动。现在重启会中断正在进行的请求。")
        else:
            box.setText("Codexio 已恢复 ChatGPT 配置" + ("，即将退出" if operation == "quit" else ""))
            box.setInformativeText("如果不重启 ChatGPT 可能无法正常运行。现在重启会中断正在进行的请求。")
        later = box.addButton("稍后自行重启", QMessageBox.ButtonRole.RejectRole)
        now = box.addButton("现在重启", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(later)
        box.setEscapeButton(later)
        box.exec()
        self.busy = False
        if box.clickedButton() == now:
            self._run("restart", self.service.restart_client, (operation, after))
        else:
            self._finish_action(operation, after, deferred=True)

    def _run(self, operation, work, after=None):
        if self.busy or self.closing:
            return
        self.busy = True
        self._status({"enable": "正在应用上游检测设置…", "disable": "正在恢复 ChatGPT 配置…",
                      "quit": "正在恢复 ChatGPT 配置并停止代理…", "startup": "正在应用启动设置…",
                      "recover": "正在恢复直连配置…", "restart": "正在重启 ChatGPT…"}[operation])
        def run():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, str(exc) if isinstance(exc, UpstreamError) else "操作未完成，请重试"
            self._done.emit((operation, value, error, after))
        threading.Thread(target=run, name="Codexio-upstream", daemon=True).start()

    def start(self):
        self._timer.start()
        if self.mock:
            self._status("预览模式不启用上游检测")
            self.startup_ready.emit(False)
            return
        update = bool(os.environ.get("CODEXIO_UPDATE_JOB"))
        enabled = bool(self.config().get("upstream_detection_enabled"))
        def start():
            handed_off = self.service.recover(update=update)
            if enabled:
                if not handed_off:
                    self.service.enable()
                return handed_off
            if self.service.state or self.service.needs_restore:
                self.service.disable()
            return False
        self._run("startup", start)

    def toggle(self, enabled):
        if self.busy or self.closing:
            return
        if self.mock:
            self._status("预览模式不修改 Codex 配置")
            return
        if enabled:
            if not self.active:
                self._run("enable", self.service.enable)
        else:
            self._persist(upstream_detection_enabled=False)
            if self.active or self.service.needs_restore:
                self._run("disable", self.service.disable)
            else:
                self._status("已关闭 · 官方直连")

    def _finished(self, result):
        operation, value, error, after = result
        if self.closing:
            return
        if operation == "health":
            self._polling = False
            if error and self.active and not self.busy:
                self._run("recover", self.service.disable)
            return
        self.busy = False
        self.active = self.service.active
        if error:
            self._status(error)
            if operation == "restart" and after[0] == "quit":
                get_logger("upstream").warning("退出时自动重启失败：%s", error)
                self._exit(after[1])
                return
            QMessageBox.warning(self.parent_window(), "上游检测", error)
            if operation == "startup":
                self.startup_ready.emit(True)
            if operation == "restart" and after[0] == "startup":
                self.startup_ready.emit(True)
            return
        if operation == "enable":
            self._persist(upstream_detection_enabled=True)
        if self._pending_quit and operation not in ("quit", "restart"):
            callback, self._pending_quit = self._pending_quit, None
            self.request_quit(callback)
            return
        if operation == "startup":
            if self.active and not value:
                self._offer_restart(operation)
            else:
                self._finish_action(operation)
        elif operation == "enable":
            self._offer_restart(operation)
        elif operation == "disable":
            if value:
                self._offer_restart(operation)
            else:
                self._finish_action(operation)
        elif operation == "quit":
            self._offer_restart(operation, after)
        elif operation == "restart":
            self._finish_action(*after, restarted=bool(value))
        elif operation == "recover":
            self._status("代理已停止并恢复配置，请重新开启上游检测")

    def _finish_action(self, operation, after=None, *, deferred=False, restarted=False):
        self._status(("已开启" if self.active else "已关闭 · 官方直连") +
                     (" · 请自行重启 ChatGPT" if deferred else " · 已重启 ChatGPT" if restarted else ""))
        if operation == "quit":
            self._exit(after)
        elif self._pending_quit:
            callback, self._pending_quit = self._pending_quit, None
            self.request_quit(callback)
        elif operation == "startup":
            self.startup_ready.emit(self.active)

    def request_quit(self, callback):
        if self.closing:
            return
        if self.busy:
            self._pending_quit = callback
            return
        if (self.active or self.service.needs_restore) and not self.system_exit:
            self._run("quit", self.service.disable, callback)
        else:
            self._exit(callback)

    def _exit(self, callback):
        self.allow_quit = True
        if callback:
            callback()

    def quit_for_update(self, callback):
        if self.busy:
            raise UpstreamError("上游检测正在切换，稍后再更新")
        self.service.prepare_update()
        self._exit(callback)

    def _system_quit(self, _session):
        self.system_exit = self.allow_quit = True
        self.stop()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Quit and not self.allow_quit and not self.system_exit and self.on_quit:
            self.request_quit(self.on_quit)
            return True
        return super().eventFilter(watched, event)

    def _poll(self):
        revision = self.store.revision()
        if revision != self._revision:
            self._revision = revision
            self.observations_changed.emit()
        if not self.active or self.busy or self.closing or self._polling:
            return
        self._polling = True
        def check():
            error = None
            try:
                if not self.service.healthy():
                    error = "检测已暂停"
            except UpstreamError:
                error = "转发服务中断"
            self._done.emit(("health", None, error, None))
        threading.Thread(target=check, name="Codexio-upstream-health", daemon=True).start()

    def stop(self):
        if self.closing:
            return
        self.closing = True
        self._timer.stop()
        if not self.mock:
            try:
                self.service.abandon()
            except (UpstreamError, OSError):
                pass
