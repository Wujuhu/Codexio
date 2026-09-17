from __future__ import annotations

import os
import gc
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QScrollArea, QTableWidget, QWidget

from codexio.theme import apply_theme


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def area(app):
    area = QScrollArea()
    content = QWidget()
    content.resize(1200, 2000)
    area.setWidget(content)
    area.resize(300, 180)
    apply_theme(area, "dark")
    area.show()
    app.processEvents()
    yield area
    area.close()
    area.deleteLater()
    app.processEvents()


def wheel(app, area, phase=Qt.ScrollPhase.ScrollUpdate):
    position = QPoint(30, 30)
    delta = QPoint() if phase == Qt.ScrollPhase.ScrollEnd else QPoint(0, -120)
    event = QWheelEvent(QPointF(position), QPointF(area.viewport().mapToGlobal(position)), QPoint(), delta,
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, phase, False)
    app.sendEvent(area.viewport(), event)
    app.processEvents()


def test_indicator_stays_for_three_seconds_after_last_scroll_then_disappears_without_reflow(app, area):
    bar = area.verticalScrollBar()
    assert bar.maximum() > 0 and bar.property("scrollActive") is False
    geometry = area.viewport().geometry()
    wheel(app, area)
    assert bar.value() > 0 and bar.property("scrollActive") is True
    QTest.qWait(1500)
    wheel(app, area, Qt.ScrollPhase.ScrollEnd)
    QTest.qWait(1550)  # The first scroll's deadline has passed, but ScrollEnd reset it.
    assert bar.property("scrollActive") is True
    QTest.qWait(1550)
    assert bar.property("scrollActive") is False
    assert area.viewport().geometry() == geometry
    assert area.horizontalScrollBar().property("scrollActive") is False


def test_horizontal_scroll_and_drag_keep_indicator_until_release(app, area):
    bar = area.horizontalScrollBar()
    bar.setValue(80)
    assert bar.property("scrollActive") is True
    bar.setSliderDown(True)
    assert not bar._codexio_indicator.timer.isActive()
    bar.setValue(120)
    assert bar.property("scrollActive") is True
    bar.setSliderDown(False)
    assert bar._codexio_indicator.timer.isActive()
    area.hide()
    assert bar.property("scrollActive") is False
    area.show()
    app.processEvents()
    assert bar.property("scrollActive") is False


@pytest.mark.parametrize("kind", ["table", "editor"])
def test_dynamically_created_scroll_areas_and_theme_changes_use_same_behavior(app, area, kind):
    widget = QTableWidget(200, 5) if kind == "table" else QPlainTextEdit("\n".join(str(i) for i in range(200)))
    try:
        widget.resize(240, 160)
        apply_theme(widget, "light")
        widget.show()
        app.processEvents()
        gc.collect()
        bar = widget.verticalScrollBar()
        assert bar.maximum() > 0 and bar.property("scrollActive") is False
        bar.setValue(20)
        assert bar.property("scrollActive") is True
        apply_theme(widget, "dark")
        assert bar.property("scrollActive") is True
        widget.hide()
        assert bar.property("scrollActive") is False
    finally:
        widget.close()
        widget.deleteLater()


def test_scroll_controllers_survive_gc_and_are_released_with_their_widgets(app, area):
    widget = QPlainTextEdit("row\n" * 100)
    apply_theme(widget, "dark")
    widget.show()
    app.processEvents()
    bar = widget.verticalScrollBar()
    key = id(bar)
    manager = app._codexio_scroll_indicators
    gc.collect()
    bar.setValue(20)
    assert bar.property("scrollActive") is True and key in manager._indicators
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert key not in manager._indicators
