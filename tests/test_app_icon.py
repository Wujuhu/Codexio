from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu, QWidget

from aiquota.app_icon import bundled_icon_path, load_app_icon, master_png_path
from aiquota.rate_limits import QuotaState
from aiquota.tray import TrayController


def test_bundled_app_icon_is_present() -> None:
    ico = bundled_icon_path()
    png = master_png_path()
    assert ico.is_file()
    assert png.is_file()
    assert ico.read_bytes()[:4] == b"\x00\x00\x01\x00"
    QApplication.instance() or QApplication([])
    icon = load_app_icon()
    assert icon.isNull() is False
    pixmap = icon.pixmap(32, 32)
    assert pixmap.isNull() is False
    assert pixmap.width() == 32
    image = pixmap.toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(31, 0).alpha() == 0
    assert image.pixelColor(0, 31).alpha() == 0
    assert any(
        image.pixelColor(x, y).alpha() > 16
        for x in range(6, 26)
        for y in range(6, 26)
    )


def test_tray_keeps_app_icon() -> None:
    QApplication.instance() or QApplication([])
    host = QWidget()
    tray = TrayController(host, QMenu(host), lambda: None, load_app_icon())
    assert tray._tray.icon().isNull() is False
    tray.update_state(QuotaState.empty(), show_five=True)
    assert tray._tray.icon().isNull() is False
    assert tray._tray.icon().pixmap(32, 32).isNull() is False
