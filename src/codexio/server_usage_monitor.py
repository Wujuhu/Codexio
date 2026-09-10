"""Collect independently of the window and the local/SSH indexing worker."""
from __future__ import annotations

import copy
import threading
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, Signal

from codexio.estimation import _time
from codexio.server_usage import ServerUsageClient, ServerUsageError, read_credentials
from codexio.server_usage_store import ServerUsageStore


class ServerUsageMonitor(QObject):
    updated = Signal()

    def __init__(self, config, directory, parent=None, *, mock=False, client=None):
        super().__init__(parent)
        self._config = copy.deepcopy(config)
        self._path = directory / "server_usage.sqlite"
        self._mock = mock
        self._client = client or ServerUsageClient()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._thread = None
        self._force = False
        self._store = None

    def start(self):
        if self._mock or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="Codexio-server-usage", daemon=True)
        self._thread.start()

    def update_config(self, config):
        with self._lock:
            was_enabled = self._enabled()
            self._config = copy.deepcopy(config)
            if was_enabled != self._enabled():
                self._wake.set()

    def request_refresh(self):
        with self._lock:
            self._force = True
        self._wake.set()

    def stop(self):
        with self._lock:
            self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _enabled(self):
        return not self._mock and self._config.get("server_estimates_enabled", True) is True

    def _save(self, context, **kwargs):
        with self._lock:
            if self._stop.is_set() or not self._enabled():
                return
            self._store.save(context, **kwargs)
            self.updated.emit()

    def collect_once(self):
        """Return retry delay. Injectable client keeps tests off the network."""
        now = datetime.now(timezone.utc)
        context = {}
        try:
            credentials = read_credentials()
            context = self._store.account_context(credentials.key)
            observation = self._client.quota(credentials)
            context.update(account_key=credentials.key, quota_status="ok", last_quota_at=observation["timestamp"],
                           reset_at=observation["reset_at"], plan_type=observation["plan_type"])
            self._save(context, observation=observation)
            fetched = _time(context.get("last_daily_at"))
            attempted = _time(context.get("daily_attempt_at"))
            with self._lock:
                force, self._force = self._force, False
            due = not fetched or now - fetched >= timedelta(hours=6)
            if (force or due and (not attempted or now - attempted >= timedelta(minutes=5))) and not self._stop.is_set() and self._enabled():
                context["daily_attempt_at"] = now.isoformat()
                start, end = (now - timedelta(days=89)).date(), now.date()
                try:
                    daily = self._client.daily(credentials, start, end)
                    context.update(daily_status="ok", last_daily_at=datetime.now(timezone.utc).isoformat())
                    self._save(context, daily=daily, date_range=(start.isoformat(), end.isoformat()))
                except ServerUsageError as exc:
                    context["daily_status"] = exc.code
                    # Failed identity validation must also disable cached current estimates.
                    if exc.code in ("account_mismatch", "auth_expired"):
                        context["quota_status"] = exc.code
                    self._save(context)
            # Take extra boundary samples when the app happens to be online.
            seconds = (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
            return 30 if min(seconds, 86400 - seconds) <= 180 else 60
        except ServerUsageError as exc:
            context["quota_status"] = exc.code
            if exc.code in ("auth_missing", "account_mismatch", "auth_expired"):
                context["account_key"] = None
            self._save(context)
            return 300 if exc.code in ("rate_limited", "auth_missing", "auth_expired") else 60

    def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            delay = 60
            if self._enabled():
                try:
                    if self._store is None:
                        self._store = ServerUsageStore(self._path)
                    delay = self.collect_once()
                except Exception:
                    # Never log provider payloads or exceptions carrying headers.
                    from codexio.logging_setup import get_logger
                    get_logger("server_usage").warning("服务端统计采集或缓存失败，稍后重试")
                    delay = 300
            self._wake.wait(delay)
