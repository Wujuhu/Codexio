"""Show scroll indicators while scrolling, then hide them after three seconds.

The scrollbar keeps its geometry so text and table columns never jump when an
indicator disappears. The theme paints the idle track and handle transparent.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Slot
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QScrollBar, QWidget

HIDE_DELAY_MS = 3000


class ScrollIndicator(QObject):
    def __init__(self, bar: QScrollBar):
        super().__init__(bar)
        self.bar = bar
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(HIDE_DELAY_MS)
        self.timer.timeout.connect(self.hide)
        bar.valueChanged.connect(self.reveal)
        bar.rangeChanged.connect(self._range_changed)
        bar.sliderPressed.connect(self.reveal)
        bar.sliderReleased.connect(self.reveal)

    def _set_active(self, active):
        if self.bar.property("scrollActive") == active:
            return
        self.bar.setProperty("scrollActive", active)
        style = self.bar.style()
        style.unpolish(self.bar)
        style.polish(self.bar)
        self.bar.update()

    @Slot()
    @Slot(int)
    def reveal(self, *_):
        if not self.bar.isVisible() or self.bar.maximum() <= self.bar.minimum():
            return
        self._set_active(True)
        # Holding the thumb is still scrolling; start the countdown on release.
        if self.bar.isSliderDown():
            self.timer.stop()
        else:
            self.timer.start()

    @Slot()
    def hide(self):
        if self.bar.isSliderDown():
            return
        self.timer.stop()
        self._set_active(False)

    @Slot(int, int)
    def _range_changed(self, minimum, maximum):
        if maximum <= minimum:
            self.hide()


class ScrollIndicators(QObject):
    def __init__(self, app):
        super().__init__(app)
        # Qt creates these scrollbars in C++. Keep their Python controllers
        # rooted until destroyed; a bar/controller reference cycle alone can be
        # collected while Cocoa still has live signal connections to it.
        self._indicators = {}
        app.installEventFilter(self)
        for widget in app.allWidgets():
            if isinstance(widget, QScrollBar):
                self.indicator(widget)

    def indicator(self, bar):
        key = id(bar)
        indicator = self._indicators.get(key)
        if indicator is None:
            indicator = ScrollIndicator(bar)
            self._indicators[key] = indicator
            bar._codexio_indicator = indicator
            bar.destroyed.connect(lambda _object=None, key=key: self._indicators.pop(key, None))
            indicator.hide()
        return indicator

    def eventFilter(self, watched, event):
        kind = event.type()
        if isinstance(watched, QScrollBar):
            if kind in (QEvent.Type.Show, QEvent.Type.Hide):
                self.indicator(watched).hide()
            elif kind in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel):
                self.indicator(watched).reveal()
        elif kind == QEvent.Type.Wheel and isinstance(watched, QWidget):
            area = watched
            while area is not None and not isinstance(area, QAbstractScrollArea):
                area = area.parentWidget()
            if area is not None:
                delta = event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta()
                if delta.x() or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.indicator(area.horizontalScrollBar()).reveal()
                if delta.y() and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.indicator(area.verticalScrollBar()).reveal()
                # A trackpad's ScrollEnd has no delta. Count three seconds from
                # that event, including momentum, rather than the last movement.
                if event.phase() == Qt.ScrollPhase.ScrollEnd:
                    for bar in (area.horizontalScrollBar(), area.verticalScrollBar()):
                        if bar.property("scrollActive"):
                            self.indicator(bar).reveal()
        return False


def install_transient_scrollbars():
    app = QApplication.instance()
    if app is not None and not hasattr(app, "_codexio_scroll_indicators"):
        app._codexio_scroll_indicators = ScrollIndicators(app)
