"""Keep native Windows caption colors in step with the application theme."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import os

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow


@lru_cache(maxsize=1)
def _dwm_set_attribute():
    if os.name != "nt":
        return None
    try:
        function = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
        function.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        function.restype = ctypes.c_long
        return function
    except (AttributeError, OSError):
        return None


def _colorref(value):
    color = QColor(value)
    return color.red() | (color.green() << 8) | (color.blue() << 16)


def _set_caption(handle, *, dark, background, foreground):
    function = _dwm_set_attribute()
    if function is None:
        return
    # DWM caption/text colors are COLORREF values, not Qt's ARGB format.
    # Older Windows versions can reject optional attributes independently.
    for attribute, value in ((20, int(dark)), (35, _colorref(background)), (36, _colorref(foreground))):
        payload = wintypes.DWORD(value)
        function(handle, attribute, ctypes.byref(payload), ctypes.sizeof(payload))


class _WindowChrome(QObject):
    def __init__(self, window):
        super().__init__(window)
        self._colors = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._apply)
        window.installEventFilter(self)

    def configure(self, *, dark, background, foreground):
        self._colors = dict(dark=dark, background=background, foreground=foreground)
        self._timer.start(0)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange):
            self._timer.start(0)
        return False

    def _apply(self):
        window = self.parent()
        if self._colors and window.windowHandle() is not None:
            _set_caption(int(window.winId()), **self._colors)


def apply_window_chrome(widget, *, dark, colors):
    """Style framed top-level app windows without creating hidden native handles."""
    app = QApplication.instance()
    if (os.name != "nt" or app is None or app.platformName() != "windows"
            or not isinstance(widget, (QMainWindow, QDialog))
            or widget.windowFlags() & Qt.WindowType.FramelessWindowHint):
        return
    controller = getattr(widget, "_theme_window_chrome", None)
    if controller is None:
        controller = _WindowChrome(widget)
        widget._theme_window_chrome = controller
    controller.configure(dark=dark, background=colors["sidebar_start"], foreground=colors["text"])
