import ctypes
from ctypes import wintypes
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMainWindow

from codexio import window_chrome
from codexio.theme import theme_colors


def test_native_caption_receives_both_theme_states_and_colorref_order(monkeypatch):
    calls = []
    def write(handle, attribute, payload, size):
        calls.append((handle, attribute, ctypes.cast(payload, ctypes.POINTER(wintypes.DWORD)).contents.value, size))
        return -1 if attribute == 35 else 0
    monkeypatch.setattr(window_chrome, "_dwm_set_attribute", lambda: write)
    for name in ("light", "dark", "light"):
        colors = theme_colors(name)
        window_chrome._set_caption(42, dark=name == "dark", background=colors["sidebar_start"], foreground=colors["text"])
    assert [value for _, attribute, value, _ in calls if attribute == 20] == [0, 1, 0]
    assert calls[1][2] == 0xF1EDED
    assert calls[2][2] == 0x2B2525
    assert len(calls) == 9


def test_native_caption_reapplies_after_window_creation_and_show(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    calls = []
    monkeypatch.setattr(window_chrome, "_set_caption", lambda handle, **colors: calls.append((handle, colors)))
    controller = window_chrome._WindowChrome(window)
    controller.configure(dark=False, background="#EDEDF1", foreground="#25252B")
    app.processEvents()
    assert not calls
    window.winId()
    app.processEvents()
    assert calls[-1][1]["dark"] is False
    controller.configure(dark=True, background="#202022", foreground="#F0F0F3")
    app.processEvents()
    assert calls[-1][1]["dark"] is True
    calls.clear()
    controller.eventFilter(window, QEvent(QEvent.Type.Show))
    app.processEvents()
    assert calls[-1][1]["dark"] is True
    window.close()
