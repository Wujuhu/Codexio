"""Coordinate background updates with the application's normal shutdown."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from aiquota import __version__
from aiquota.logging_setup import get_logger
from aiquota.update_installer import (
    _state, cancel_job, cleanup_updates, commit_job, launch_installer, new_job_dir,
)
from aiquota.updates import UpdateCancelled, UpdateError, download_release, fetch_release


class UpdateManager(QObject):
    status_changed = Signal(str, bool)
    _event = Signal(object)

    def __init__(self, parent: QObject, on_restart: Callable[[], None], *, available: bool | None = None) -> None:
        super().__init__(parent)
        self._on_restart = on_restart
        self._available = bool(getattr(sys, "frozen", False) and os.name == "nt") if available is None else available
        self._enabled = True
        self._busy = False
        self._manual = False
        self._closing = False
        self._committed = False
        self._job: Path | None = None
        self._cancel = threading.Event()
        self._logger = get_logger("updates")
        self._event.connect(self._handle_event)
        self._timer = QTimer(self)
        self._timer.setInterval(6 * 60 * 60 * 1000)
        self._timer.timeout.connect(self._automatic_check)
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setInterval(5 * 60 * 1000)
        self._cleanup_timer.timeout.connect(self._cleanup)

    def start(self, enabled: bool = True) -> None:
        self._enabled = enabled
        if not self._available:
            self.status_changed.emit("源码和预览模式不安装更新", True)
            return
        self._timer.start()
        self._cleanup_timer.start()
        self.status_changed.emit("启动后自动检查新版" if enabled else "自动更新已关闭", False)
        if os.environ.pop("AIQUOTA_SKIP_UPDATE_ONCE", "") != "1":
            QTimer.singleShot(5000, self._automatic_check)

    def set_enabled(self, enabled: bool) -> None:
        changed = self._enabled != enabled
        self._enabled = enabled
        if not enabled and self._busy and not self._manual and not self._committed:
            self._cancel.set()
            if self._job:
                cancel_job(self._job)
                self._job = None
                self._busy = False
        elif changed and enabled and not self._busy:
            QTimer.singleShot(500, self._automatic_check)
        if not self._busy and self._available:
            self.status_changed.emit("自动更新已开启" if enabled else "自动更新已关闭", False)

    def _automatic_check(self) -> None:
        if self._enabled and not self._closing:
            self.check(manual=False)

    def check(self, manual: bool = True) -> None:
        if self._closing or self._busy or not self._available:
            return
        self._busy = True
        self._manual = manual
        self._job = None
        self._committed = False
        self._cancel = threading.Event()
        self.status_changed.emit("正在检查新版…", True)
        threading.Thread(target=self._run, name="AIQuota-update", daemon=True).start()

    def _emit(self, event) -> None:
        if not self._closing:
            self._event.emit(event)

    def _run(self) -> None:
        directory = None
        try:
            release = fetch_release(__version__)
            if self._cancel.is_set():
                raise UpdateCancelled("已取消更新")
            if release is None:
                self._emit(("finished", "当前已是最新版本，或尚未发布新版"))
                return
            directory = new_job_dir()
            self._emit(("progress", "发现新版 %s，正在下载…" % release.version))
            download_release(release, directory / "package.bin", self._cancel,
                             lambda value: self._emit(("progress", "正在下载 %s · %d%%" % (release.version, value))))
            self._emit(("progress", "校验通过，正在准备安装…"))
            job = launch_installer(directory, release, self._cancel)
            self._emit(("ready", job))
        except UpdateCancelled as exc:
            if directory:
                _state(directory, "cancelled", str(exc))
            self._emit(("finished", "已取消自动更新"))
        except Exception as exc:
            message = str(exc) if isinstance(exc, UpdateError) else "更新失败，请稍后重试"
            self._logger.warning("更新未完成：%s", exc)
            if directory:
                _state(directory, "failed", message)
            self._emit(("finished", message))

    @Slot(object)
    def _handle_event(self, event) -> None:
        kind, value = event
        if self._closing:
            if kind == "ready":
                cancel_job(value)
            return
        if kind == "ready":
            self._job = value
            if self._cancel.is_set() or (not self._manual and not self._enabled):
                cancel_job(value)
                self._busy = False
                self.status_changed.emit("已取消自动更新", False)
                return
            self.status_changed.emit("新版已就绪，即将自动重启…", True)
            QTimer.singleShot(1200, self._commit_install)
        elif kind == "progress":
            self.status_changed.emit(value, True)
        else:
            self._busy = False
            self.status_changed.emit(value, False)

    def _commit_install(self) -> None:
        if self._closing or not self._job or self._cancel.is_set():
            return
        try:
            commit_job(self._job)
            self._committed = True
            self._on_restart()
        except Exception as exc:
            cancel_job(self._job)
            self._committed = False
            self._busy = False
            self.status_changed.emit("未能开始安装：" + str(exc), False)

    def _cleanup(self) -> None:
        try:
            cleanup_updates()
        except OSError:
            pass

    def stop(self) -> None:
        self._closing = True
        self._timer.stop()
        self._cleanup_timer.stop()
        self._cancel.set()
        if self._job and not self._committed:
            cancel_job(self._job)
