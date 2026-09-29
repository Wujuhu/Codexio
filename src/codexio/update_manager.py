"""Coordinate background updates with the application's normal shutdown."""
from __future__ import annotations

from codexio.i18n import tr

import os
import json
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QProgressBar, QTextBrowser, QVBoxLayout

from codexio import __version__
from codexio.logging_setup import get_logger
from codexio.settings import data_dir
from codexio.update_installer import (
    _state, cancel_job, cleanup_updates, commit_job, launch_installer, new_job_dir,
)
from codexio.updates import UpdateCancelled, UpdateError, download_release, fetch_release

if sys.platform == "darwin":
    from codexio.macos_updater import cleanup_updates, download_release, fetch_release, launch_installer


class UpdateManager(QObject):
    status_changed = Signal(str, bool)
    _event = Signal(object)

    def __init__(self, parent: QObject, on_restart: Callable[[], None], *, available: bool | None = None) -> None:
        super().__init__(parent)
        self._on_restart = on_restart
        self._available = bool(getattr(sys, "frozen", False) and (os.name == "nt" or sys.platform == "darwin")) if available is None else available
        self.is_resolving_startup = self._available
        self._enabled = True
        self._busy = False
        self._manual = False
        self._closing = False
        self._committed = False
        self._job: Path | None = None
        self._release = None
        self._dialog: QDialog | None = None
        self._dialog_title: QLabel | None = None
        self._progress_bar: QProgressBar | None = None
        self._message: QLabel | None = None
        self._download_link: QLabel | None = None
        self._window_provider = lambda: None
        self._presentation_finished = lambda: None
        self._explicit = False
        self._cancel = threading.Event()
        self._logger = get_logger("updates")
        self._event.connect(self._handle_event)
        self._timer = QTimer(self)
        self._timer.setInterval(60 * 60 * 1000)
        self._timer.timeout.connect(self._automatic_check)
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setInterval(5 * 60 * 1000)
        self._cleanup_timer.timeout.connect(self._cleanup)

    def start(self, enabled: bool = True) -> None:
        self._enabled = enabled
        if not self._available:
            self.is_resolving_startup = False
            self.status_changed.emit(tr("源码和预览模式不安装更新"), True)
            return
        self._timer.start()
        self._cleanup_timer.start()
        self.status_changed.emit(tr("启动后自动检查新版") if enabled else tr("自动更新已关闭"), False)
        if not enabled:
            self.is_resolving_startup = False
        if os.environ.pop("CODEXIO_SKIP_UPDATE_ONCE", "") != "1":
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
            self.status_changed.emit(tr("自动更新已开启") if enabled else tr("自动更新已关闭"), False)

    def set_presentation(self, window_provider, on_finished=lambda: None) -> None:
        self._window_provider = window_provider
        self._presentation_finished = on_finished

    def _deferred(self, version: str) -> bool:
        try:
            content = json.loads((data_dir() / "updates" / "reminders.json").read_text(encoding="utf-8"))
            return float(content.get(version, 0)) > time.time()
        except (OSError, ValueError, TypeError):
            return False

    def _defer(self) -> None:
        if self._release is not None:
            target = data_dir() / "updates" / "reminders.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                content = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                content = {}
            content = {key: value for key, value in content.items() if isinstance(value, (int, float)) and value > time.time()}
            content[self._release.version] = time.time() + 86_400
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
        self._explicit = False
        if self._dialog is not None:
            self._dialog.close()

    @staticmethod
    def _notes(version: str) -> str:
        url = "https://api.github.com/repos/Wujuhu/Codexio/releases/tags/v" + version
        request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Codexio/" + __version__})
        try:
            with urlopen(request, timeout=10) as response:
                value = json.loads(response.read(256_000).decode("utf-8"))
            if value.get("tag_name") == "v" + version and not value.get("draft"):
                return str(value.get("body") or "")[:4000]
        except (OSError, ValueError, TypeError):
            pass
        return ""

    def _automatic_check(self) -> None:
        if self._enabled and not self._closing:
            self.check(manual=False)

    def check(self, manual: bool = True) -> None:
        if self._closing or self._busy or not self._available:
            return
        if manual:
            self._explicit = True
        self._busy = True
        self._manual = manual
        self._job = None
        self._committed = False
        self._cancel = threading.Event()
        self.status_changed.emit(tr("正在检查新版…"), True)
        threading.Thread(target=self._run_check, name="Codexio-update-check", daemon=True).start()

    def _emit(self, event) -> None:
        if not self._closing:
            self._event.emit(event)

    def _run_check(self) -> None:
        try:
            release = fetch_release(__version__)
            if self._cancel.is_set():
                raise UpdateCancelled(tr("已取消更新"))
            if release is not None:
                release = type(release)(release.version, release.url, release.sha256, release.size, self._notes(release.version))
            self._emit(("available", release))
        except Exception as exc:
            self._emit(("finished", str(exc) if isinstance(exc, UpdateError) else tr("检查更新失败")))

    def _run_install(self, release) -> None:
        directory = None
        try:
            directory = new_job_dir()
            self._emit(("progress", 0))
            download_release(release, directory / "package.bin", self._cancel,
                             lambda value: self._emit(("progress", value)))
            self._emit(("installing", None))
            job = launch_installer(directory, release, self._cancel)
            self._emit(("ready", job))
        except UpdateCancelled as exc:
            if directory:
                _state(directory, "cancelled", str(exc))
                try:
                    (directory / "package.bin").unlink(missing_ok=True)
                except OSError:
                    pass
            self._emit(("failure", (tr("已取消更新"), release)))
        except Exception as exc:
            message = str(exc) if isinstance(exc, UpdateError) else tr("更新失败，请稍后重试")
            self._logger.warning(tr("更新未完成：%s"), exc)
            if directory:
                _state(directory, "failed", message)
                try:
                    (directory / "package.bin").unlink(missing_ok=True)
                except OSError:
                    pass
            self._emit(("failure", (message, release)))

    def present_if_possible(self) -> bool:
        window = self._window_provider()
        if (self._closing or self._dialog is not None or self._release is None or window is None
                or not window.isVisible() or not window.isActiveWindow()
                or QApplication.activeModalWidget() is not None
                or not (self._explicit or self._busy or not self._deferred(self._release.version))):
            return False
        release = self._release
        dialog = QDialog(window)
        dialog.setWindowTitle(tr("发现新版本"))
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        title = QLabel(tr("发现新版本 %s") % release.version)
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)
        self._dialog_title = title
        if release.notes.strip():
            notes = QTextBrowser()
            notes.setMarkdown(release.notes)
            notes.setReadOnly(True)
            notes.setMaximumHeight(210)
            layout.addWidget(notes)
        self._message = QLabel(tr("是否现在更新？"))
        self._message.setWordWrap(True)
        layout.addWidget(self._message)
        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.hide()
        self._progress_bar = progress
        layout.addWidget(progress)
        link = QLabel()
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        link.setOpenExternalLinks(True)
        link.hide()
        self._download_link = link
        layout.addWidget(link)
        row = QHBoxLayout()
        row.addStretch()
        later = QPushButton(tr("稍后"))
        later.clicked.connect(self._defer)
        row.addWidget(later)
        self._later_button = later
        update = QPushButton(tr("更新"))
        update.setDefault(True)
        update.clicked.connect(self._begin_install)
        row.addWidget(update)
        layout.addLayout(row)
        self._dialog = dialog
        dialog.finished.connect(lambda *_: self._finished_presenting(dialog))
        dialog.show()
        self._explicit = False
        return True

    def _finished_presenting(self, dialog) -> None:
        if self._dialog is dialog:
            self._dialog = None
        if self._busy and not self._committed:
            self._cancel.set()
        self._presentation_finished()

    def _begin_install(self) -> None:
        if self._release is None or self._busy or self._closing:
            return
        self._busy = self._manual = True
        self._cancel = threading.Event()
        self._later_button.setEnabled(False)
        self._progress_bar.show()
        self._message.setText(tr("正在下载并校验安装包…"))
        self._download_link.hide()
        self.status_changed.emit(tr("正在下载 %s") % self._release.version, True)
        threading.Thread(target=self._run_install, args=(self._release,), name="Codexio-update-download", daemon=True).start()

    @Slot(object)
    def _handle_event(self, event) -> None:
        kind, value = event
        if self._closing:
            if kind == "ready":
                cancel_job(value)
            return
        if kind == "available":
            self._busy = False
            self.is_resolving_startup = False
            self._release = value
            self.status_changed.emit((tr("有新版本 %s") % value.version) if value else tr("已是最新版本。"), False)
            if value:
                if not self.present_if_possible():
                    self._presentation_finished()
            else:
                self._explicit = False
                self._presentation_finished()
            return
        if kind == "ready":
            self._job = value
            if self._cancel.is_set() or (not self._manual and not self._enabled):
                cancel_job(value)
                self._busy = False
                self.status_changed.emit(tr("已取消自动更新"), False)
                return
            self.status_changed.emit(tr("新版已就绪，即将自动重启…"), True)
            if self._message is not None:
                self._message.setText(tr("校验通过，准备安装并重新启动…"))
            QTimer.singleShot(1200, self._commit_install)
        elif kind == "progress":
            self.status_changed.emit(tr("正在下载 %s · %d%%") % (self._release.version, value), True)
            if self._progress_bar is not None:
                self._progress_bar.setValue(int(value))
        elif kind == "installing":
            self.status_changed.emit(tr("校验通过，正在准备安装…"), True)
            if self._message is not None:
                self._message.setText(tr("校验通过，正在准备安装…"))
        elif kind == "failure":
            self._busy = False
            message, release = value
            self.status_changed.emit(message, False)
            if self._dialog is not None:
                self._message.setText(message)
                self._later_button.setEnabled(True)
                self._progress_bar.hide()
                self._download_link.setText('<a href="%s">%s</a>' % (release.url, tr("从 GitHub 直接下载 Codexio.exe")))
                self._download_link.show()
        else:
            self._busy = False
            self.is_resolving_startup = False
            self.status_changed.emit(value, False)
            self._presentation_finished()

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
            self.status_changed.emit(tr("未能开始安装：") + str(exc), False)
            if self._dialog is not None and self._release is not None:
                self._message.setText(tr("未能开始安装：") + str(exc))
                self._progress_bar.hide()
                self._later_button.setEnabled(True)
                self._download_link.setText('<a href="%s">%s</a>' % (self._release.url, tr("从 GitHub 直接下载 Codexio.exe")))
                self._download_link.show()

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
        if self._dialog is not None:
            self._dialog.close()
