from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent

from codexio.hit_test import (
    HIT_BUTTON,
    HIT_DRAG,
    HIT_RESIZE,
    chrome_hit,
    orb_contains,
    orb_resize_edge,
    resize_edge_on_ring,
)


def test_resize_edge_follows_the_nearest_side() -> None:
    rect = QRect(0, 0, 320, 200)
    assert resize_edge_on_ring(QPoint(6, 100), rect) == "l"
    assert resize_edge_on_ring(QPoint(160, 6), rect) == "t"
    assert resize_edge_on_ring(QPoint(6, 6), rect) == "tl"
    assert resize_edge_on_ring(QPoint(314, 194), rect) == "br"
    assert resize_edge_on_ring(QPoint(160, 100), rect) == ""
    assert resize_edge_on_ring(QPoint(160, 6), rect, blocked="t") == ""
    assert resize_edge_on_ring(QPoint(160, 194), rect, blocked="t") == "b"


def test_chrome_hit_interior_is_drag() -> None:
    window = QRect(0, 0, 320, 200)
    button = QRect(260, 12, 40, 20)

    assert chrome_hit(QPoint(80, 20), window, button) == HIT_DRAG
    assert chrome_hit(QPoint(160, 120), window, button) == HIT_DRAG
    assert chrome_hit(QPoint(160, 180), window, button) == HIT_DRAG
    assert chrome_hit(QPoint(4, 100), window, button) == HIT_RESIZE
    assert chrome_hit(QPoint(0, 0), window, button) == HIT_RESIZE
    assert chrome_hit(button.center(), window, button) == HIT_BUTTON
    assert chrome_hit(QPoint(-1, 10), window, button) is None


def test_orb_rim_is_resize_and_center_is_inside() -> None:
    rect = QRect(0, 0, 160, 160)
    assert orb_contains(QPoint(80, 80), rect) is True
    assert orb_contains(QPoint(2, 2), rect) is False
    assert orb_resize_edge(QPoint(80, 80), rect) == ""
    assert orb_resize_edge(QPoint(154, 80), rect) == "r"
    assert orb_resize_edge(QPoint(80, 154), rect) == "b"
    assert orb_resize_edge(QPoint(8, 80), rect) == "l"


def test_window_chrome_and_default_size(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from PySide6.QtWidgets import QApplication

    from codexio.settings import AppSettings
    from codexio.window import QuotaWindow

    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.resize(320, 200)
    window.show()
    app.processEvents()
    assert (window.width(), window.height()) == (320, 200)
    assert window.sizeHint().width() == 320
    assert window.sizeHint().height() == 200

    header_center = window._header.mapTo(window, window._header.rect().center())
    assert window.hit_kind_at(header_center) == HIT_DRAG
    assert window.hit_kind_at(QPoint(5, window.height() // 2)) == HIT_RESIZE
    assert window.hit_kind_at(window._button_hit_rect().center()) == HIT_BUTTON
    stack_center = window._stack.mapTo(window, window._stack.rect().center())
    assert window.hit_kind_at(stack_center) == HIT_DRAG

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(stack_center),
        QPointF(window.mapToGlobal(stack_center)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.mousePressEvent(press)
    assert window._drag_offset is not None
    window.close()
