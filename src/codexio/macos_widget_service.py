"""Resident widget monitor that shares the app's local scanner and quota reader."""
from __future__ import annotations

import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QTimer

from codexio.logging_setup import get_logger, setup_logging
from codexio.settings import data_dir

LABEL = "com.wujuhu.codexio.widget-refresh"


def ensure_widget_agent() -> None:
    """Install one user LaunchAgent; the widget and app retain stable IDs."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return
    executable = Path(sys.executable).resolve()
    if executable.parents[2] != Path("/Applications/Codexio.app"):
        return
    logger = get_logger("widget")
    agent = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    payload = plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(executable), "--widget-refresh"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
    })
    try:
        agent.parent.mkdir(parents=True, exist_ok=True)
        previous = agent.read_bytes() if agent.exists() else None
        domain = f"gui/{os.getuid()}"
        if previous != payload:
            if previous is not None:
                subprocess.run(["launchctl", "bootout", domain, str(agent)], capture_output=True, check=False)
            temporary = agent.with_suffix(".plist.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, agent)
        loaded = subprocess.run(["launchctl", "print", f"{domain}/{LABEL}"], capture_output=True, check=False)
        if loaded.returncode:
            result = subprocess.run(["launchctl", "bootstrap", domain, str(agent)], capture_output=True, check=False)
            if result.returncode:
                logger.warning("小组件后台刷新服务未启动: %s", result.stderr.decode("utf-8", errors="replace")[:240])
        else:
            # Pick up the helper binary from an in-place APP update.
            subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"],
                           capture_output=True, check=False)
    except OSError:
        logger.exception("安装小组件后台刷新服务失败")


def _main_app_running(directory: Path) -> bool:
    # Reading the GUI's lock avoids briefly taking it and racing GUI startup.
    try:
        pid = int((directory / "instance.lock").read_text(encoding="utf-8").splitlines()[0])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


class WidgetMonitor(QObject):
    def __init__(self, app: QCoreApplication):
        super().__init__(app)
        self.directory = data_dir()
        self.usage = None
        self.quota = None
        self.usage_data = None
        self.quota_state = None
        self.retired_workers = []
        self.signature = None
        self.request_key = None
        self.last_reload = 0.0
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.timeout.connect(self._reload)
        self.owner_timer = QTimer(self)
        self.owner_timer.setInterval(2000)
        self.owner_timer.timeout.connect(self._sync_owner)
        self.owner_timer.start()
        QTimer.singleShot(0, self._sync_owner)

    def _sync_owner(self):
        if _main_app_running(self.directory):
            if self.usage is not None:
                self._stop_workers()
        elif self.usage is None:
            self._start_workers()

    def _start_workers(self):
        from codexio.analytics_config import load_analytics_config
        from codexio.settings import load_settings
        from codexio.usage_worker import UsageWorker
        from codexio.worker import QuotaWorker
        config = load_analytics_config()
        config.update(ssh_sources=[], auto_sync_prices=False, server_estimates_enabled=False)
        self.usage_data = self.quota_state = None
        self.usage = UsageWorker(config, widget_only=True)
        self.quota = QuotaWorker(load_settings())
        self.usage.data_changed.connect(self._on_usage)
        self.quota.state_changed.connect(self._on_quota)
        self.usage.start()
        self.quota.start()

    def _stop_workers(self):
        self.reload_timer.stop()
        usage, quota = self.usage, self.quota
        usage.stop()
        quota.stop()
        for worker in (usage, quota):
            if worker.isRunning() and not worker.wait(30000):
                self.retired_workers.append(worker)
                get_logger("widget").warning("小组件检测线程仍在结束中")
        self.usage = self.quota = None
        self.usage_data = self.quota_state = None
        self.signature = self.request_key = None

    def _on_usage(self, data):
        if self.usage is not None:
            self.usage_data = data
            self._publish()

    def _on_quota(self, state):
        if self.quota is not None:
            self.quota_state = state
            self._publish()

    def _reload(self):
        if self.usage is None or _main_app_running(self.directory):
            return
        from codexio.macos_widget_snapshot import reload_widget
        if reload_widget():
            self.last_reload = time.monotonic()

    def _publish(self):
        if self.usage_data is None or _main_app_running(self.directory):
            return
        from codexio.macos_widget_snapshot import make_snapshot, write_snapshot
        try:
            snapshot = make_snapshot(self.usage_data, self.quota_state)
            signature = write_snapshot(snapshot)
        except (OSError, ValueError):
            get_logger("widget").exception("独立刷新小组件数据失败")
            return
        if signature == self.signature:
            return
        request = snapshot.get("request") or {}
        key = (request.get("id"), request.get("model"), request.get("reasoning_effort"), request.get("duration_running"))
        urgent = key != self.request_key
        self.signature, self.request_key = signature, key
        elapsed = time.monotonic() - self.last_reload
        if urgent or elapsed >= 30:
            self.reload_timer.stop()
            self._reload()
        elif not self.reload_timer.isActive():
            self.reload_timer.start(max(1000, int((30 - elapsed) * 1000)))

    def stop(self):
        self.owner_timer.stop()
        if self.usage is not None:
            self._stop_workers()


def run_widget_monitor() -> int:
    """Keep local usage and quota fresh after the GUI exits without a GUI window."""
    setup_logging()
    app = QCoreApplication([sys.argv[0], "--widget-refresh"])
    app.setApplicationName("CodexioWidgetMonitor")
    monitor = WidgetMonitor(app)
    app.aboutToQuit.connect(monitor.stop)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    return app.exec()
