"""Keep background state independent from the disposable main window."""
from __future__ import annotations

from PySide6.QtCore import QObject, QByteArray


class DashboardHost(QObject):
    def __init__(self, settings, config, callbacks, parent=None, *, factory=None):
        super().__init__(parent)
        self._settings = settings
        self._config = config
        self._callbacks = dict(callbacks)
        self._factory = factory
        self.dashboard = None
        self._data = None
        self._quota = None
        self._loading = None
        self._progress = None
        self._update = None
        self._generation = 0
        self._view_state = {}

    def open(self, page="overview", period=None):
        if self.dashboard is None:
            if self._factory is None:
                from codexio.dashboard import Dashboard
                factory = Dashboard
            else:
                factory = self._factory
            self._generation += 1
            generation = self._generation
            callbacks = dict(self._callbacks, main_closed=self._closed)
            window = factory(self._settings(), self._config(), callbacks)
            self.dashboard = window
            window.restore_view_state(self._view_state)
            window.destroyed.connect(lambda *_: self._destroyed(generation))
            geometry = self._config().get("main_geometry")
            if isinstance(geometry, str):
                try:
                    window.restoreGeometry(QByteArray.fromHex(geometry.encode("ascii")))
                except (ValueError, UnicodeError):
                    pass
            if self._loading is not None:
                window.set_usage_loading(self._loading)
            if self._data is not None:
                window.apply_data(self._data)
            if self._quota is not None:
                window.apply_quota(self._quota)
            if self._progress is not None:
                window.set_progress(self._progress)
            if self._update is not None:
                window.set_update_status(*self._update)
        self.dashboard.open_page(page, period)
        return self.dashboard

    def _closed(self, window):
        if self.dashboard is window:
            self._view_state = window.capture_view_state()
            self.dashboard = None

    def _destroyed(self, generation):
        if generation == self._generation:
            self.dashboard = None

    def save_geometry(self):
        if self.dashboard is not None:
            callback = self._callbacks.get("main_hidden")
            if callback:
                callback(bytes(self.dashboard.saveGeometry()).hex())

    def apply_data(self, data):
        self._data = data
        if self.dashboard is not None:
            self.dashboard.apply_data(data)

    def apply_quota(self, state):
        self._quota = state
        if self.dashboard is not None:
            self.dashboard.apply_quota(state)

    def set_usage_loading(self, state):
        self._loading = dict(state)
        if self.dashboard is not None:
            self.dashboard.set_usage_loading(state)

    def set_progress(self, message):
        self._progress = message
        if self.dashboard is not None:
            self.dashboard.set_progress(message)

    def set_update_status(self, message, busy=False):
        self._update = message, busy
        if self.dashboard is not None:
            self.dashboard.set_update_status(message, busy)

    def config_updated(self, config):
        if self.dashboard is not None:
            self.dashboard.config_updated(config)
