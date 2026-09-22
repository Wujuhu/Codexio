"""Shared macOS/Windows UI lifecycle for optional upstream detection."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, QEvent, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QMessageBox

from codexio.upstream_config import UpstreamError
from codexio.upstream_service import UpstreamService
from codexio.upstream_store import UpstreamStore


ENABLE_TEXT = ("上游检测保留 ChatGPT 官方登录，通过本地转发读取响应中的模型名称。\n\n"
               "开启后会立即自动重启正在运行的 ChatGPT；未运行时不会主动打开。"
               "退出 Codexio 时会恢复直连并自动重启 ChatGPT，确保之后仍可使用。\n\n"
               "下次打开 Codexio 会再次询问，确认后重新开启检测并自动重启 ChatGPT。"
               "进行中的请求会中断。是否继续？")


class UpstreamManager(QObject):
    status_changed = Signal(str, bool, bool)
    observations_changed = Signal()
    _done = Signal(object)

    def __init__(self, app, directory, config, save, parent_window, *, mock=False, service=None):
        super().__init__(app)
        self.app, self.config, self.save, self.parent_window = app, config, save, parent_window
        self.mock = mock
        self.service = service or UpstreamService(directory)
        self.store = UpstreamStore(directory / "upstream.sqlite")
        self.active = False
        self.busy = False
        self.closing = False
        self.allow_quit = False
        self.system_exit = False
        self.on_quit = None
        self._revision = 0
        self._polling = False
        self._done.connect(self._finished)
        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._poll)
        self.app.installEventFilter(self)
        self.app.commitDataRequest.connect(self._system_quit)

    def _persist(self, **values):
        updated = dict(self.config(), **values)
        self.save(updated)

    def _status(self, message):
        self.status_changed.emit(message, self.active, self.busy)

    def _confirm(self, *, quitting=False):
        box = QMessageBox(self.parent_window())
        box.setWindowTitle("上游检测")
        box.setText("退出并恢复官方直连？" if quitting else "开启上游检测并自动重启 ChatGPT？")
        box.setInformativeText(("退出后会暂时关闭检测、恢复直连，并自动重启正在运行的 ChatGPT。"
                                "下次打开 Codexio 仍会询问是否重新开启检测。") if quitting else ENABLE_TEXT)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Ok).setText("退出并重启" if quitting else "开启并重启")
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        checkbox = QCheckBox("下次退出不再提示") if quitting else None
        if checkbox:
            box.setCheckBox(checkbox)
        accepted = box.exec() == QMessageBox.StandardButton.Ok
        if accepted and checkbox and checkbox.isChecked():
            self._persist(upstream_exit_prompt=False)
        return accepted

    def _run(self, operation, work, after=None):
        if self.busy or self.closing:
            return
        self.busy = True
        self._status({"enable": "正在开启并重启 ChatGPT…", "disable": "正在恢复直连并重启 ChatGPT…",
                      "quit": "正在恢复直连并重启 ChatGPT…", "startup": "正在检查上次路由…"}[operation])
        def run():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, str(exc) if isinstance(exc, UpstreamError) else "操作未完成；已保留恢复记录，请重试"
            self._done.emit((operation, value, error, after))
        threading.Thread(target=run, name="Codexio-upstream", daemon=True).start()

    def start(self):
        self._timer.start()
        if self.mock:
            self._status("预览模式不启用上游检测")
            return
        update = bool(os.environ.get("CODEXIO_UPDATE_JOB"))
        self._run("startup", lambda: self.service.recover(update=update))

    def toggle(self, enabled):
        if self.busy or self.closing:
            return
        if self.mock:
            self._status("预览模式不修改 Codex 配置")
            return
        if enabled:
            if self.active:
                return
            if not self._confirm():
                self._status("本次未启用；下次仍询问" if self.config().get("upstream_detection_enabled") else "已关闭")
                return
            self._run("enable", self.service.enable)
        else:
            self._persist(upstream_detection_enabled=False)
            if self.active or self.service.needs_restore:
                self._run("disable", self.service.disable)
            else:
                self._status("已关闭 · 官方直连")

    def set_exit_prompt(self, enabled):
        self._persist(upstream_exit_prompt=bool(enabled))

    def _finished(self, result):
        operation, value, error, after = result
        if operation == "health":
            self._polling = False
            if self.closing or self.busy or not self.active or not error:
                return
            self._run("startup", self.service.recover)
            return
        self.busy = False
        self.active = self.service.active
        if error:
            self._status(error)
            QMessageBox.warning(self.parent_window(), "上游检测", error)
            return
        if operation == "startup":
            if self.active:
                self._status("已接续上游检测 · ChatGPT 无需再次重启")
            elif self.config().get("upstream_detection_enabled"):
                self.toggle(True)
            else:
                self._status("已关闭 · 官方直连")
        elif operation == "enable":
            self._persist(upstream_detection_enabled=True)
            self._status(value)
        elif operation == "disable":
            self._persist(upstream_detection_enabled=False)
            self._status(value)
        elif operation == "quit":
            self._exit(after)

    def request_quit(self, callback):
        if self.busy or self.closing:
            return
        if (self.active or self.service.needs_restore) and not self.system_exit:
            if self.config().get("upstream_exit_prompt", True) and not self._confirm(quitting=True):
                return
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
        self.system_exit = True
        self.allow_quit = True
        # The guardian restores on owner exit without reopening the client.
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
                    raise UpstreamError("检测已暂停")
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
