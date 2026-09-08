from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QObject,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from aiquota.logging_setup import get_logger
from aiquota.rate_limits import QuotaState, WindowView, format_reset_time

QUOTA_FIVE_LABEL = "5 hours"
QUOTA_WEEK_LABEL = "1 week"
BUNDLED_SANS_FONT = "AnthropicSansWebText-Regular.ttf"
_FALLBACK_FAMILIES = ("Microsoft YaHei UI", "Segoe UI")
_resolved_family: Optional[str] = None
_resolved_style: Optional[str] = None
_fonts_registered = False


def display_font_css() -> str:
    names = [display_font_family(), *_FALLBACK_FAMILIES]
    return ", ".join('"%s"' % name for name in names) + ", sans-serif"


STYLE_LABELS = {
    "classic": "条形",
    "rings": "圆环",
    "tiles": "磁贴",
    "compact": "紧凑",
    "minimal": "极简",
    "orb": "水球",
}
STYLE_WINDOW_SIZES = {
    "classic": (320, 200),
    "rings": (340, 228),
    "tiles": (360, 210),
    "compact": (340, 140),
    "minimal": (280, 152),
    "orb": (148, 148),
}
ORB_STYLE = "orb"
ORB_MIN_SIZE = 72
ORB_MAX_SIZE = 400
ORB_WATER = QColor("#7DF0B5")
ORB_WATER_MID = QColor("#3DDC97")
ORB_WATER_DEEP = QColor("#1FA97A")


def bundled_font_path() -> Path:
    names = [BUNDLED_SANS_FONT]
    roots = [Path(__file__).resolve().parent / "fonts"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass) / "aiquota" / "fonts")
        roots.append(Path(meipass) / "fonts")
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent / "aiquota" / "fonts")
        roots.append(Path(sys.executable).resolve().parent / "fonts")
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return roots[0] / BUNDLED_SANS_FONT


def register_bundled_fonts() -> Optional[str]:
    global _fonts_registered, _resolved_family, _resolved_style
    if _fonts_registered and _resolved_family:
        return _resolved_family
    logger = get_logger("visuals")
    path = bundled_font_path()
    _fonts_registered = True
    if not path.is_file():
        logger.warning("未找到内嵌字体，回退系统字体: %s", path)
        return None
    try:
        font_id = QFontDatabase.addApplicationFont(str(path))
    except Exception:
        logger.exception("加载内嵌字体失败，回退系统字体: %s", path)
        return None
    if font_id < 0:
        logger.warning("Qt 拒绝内嵌字体，回退系统字体: %s", path)
        return None
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        logger.warning("内嵌字体没有可用族名，回退系统字体: %s", path)
        return None
    _resolved_family = families[0]
    styles = QFontDatabase.styles(_resolved_family)
    _resolved_style = "Text Regular" if "Text Regular" in styles else (styles[0] if styles else "")
    logger.info("已加载字体 %s / %s", _resolved_family, _resolved_style or "Regular")
    return _resolved_family


def display_font_family() -> str:
    global _resolved_family
    if _resolved_family:
        return _resolved_family
    if not _fonts_registered:
        register_bundled_fonts()
        if _resolved_family:
            return _resolved_family
    available = set(QFontDatabase.families())
    for name in ("Anthropic Sans Web", *_FALLBACK_FAMILIES):
        if name in available:
            _resolved_family = name
            return name
    _resolved_family = "Segoe UI"
    return _resolved_family


def display_font(pixel_size: int, bold: bool = False) -> QFont:
    family = display_font_family()
    style = _resolved_style or ""
    try:
        font = QFontDatabase.font(family, style, -1) if style else QFont(family)
    except Exception:
        font = QFont(family)
    if not font.family():
        font = QFont(family)
    font.setPixelSize(pixel_size)
    font.setBold(False)
    font.setWeight(QFont.Weight.Normal)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setStyleStrategy(
        QFont.StyleStrategy.PreferMatch | QFont.StyleStrategy.PreferQuality
    )
    font.setFamilies([family, *_FALLBACK_FAMILIES])
    return font


def format_percent_label(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return "%d%%" % int(round(value))


def quota_hover_text(title: str, view: WindowView) -> str:
    if view.remaining_percent is None:
        return "%s  N/A" % title
    return "%s  %s\n重置 %s" % (
        title,
        format_percent_label(view.remaining_percent),
        format_reset_time(view.resets_at),
    )


def resolve_orb_quota(state: QuotaState, *, week_only: bool = False) -> Tuple[str, WindowView]:
    if not week_only and state.five_hour.remaining_percent is not None:
        return QUOTA_FIVE_LABEL, state.five_hour
    return QUOTA_WEEK_LABEL, state.week


class PercentAnimator(QObject):
    value_changed = Signal(object)

    def __init__(self, parent: Optional[QObject] = None, duration: int = 520) -> None:
        super().__init__(parent)
        self._display: Optional[float] = None
        self._target: Optional[int] = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_step)
        self._anim.finished.connect(self._on_finished)

    @property
    def display(self) -> Optional[float]:
        return self._display

    def set_percent(self, percent: Optional[int]) -> None:
        if percent is None:
            self._target = None
            self._anim.stop()
            self._display = None
            self.value_changed.emit(None)
            return
        if percent == self._target and self._display is not None:
            return
        start = 0.0 if self._display is None else float(self._display)
        self._target = percent
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(float(percent))
        self._anim.start()

    def _on_step(self, value) -> None:
        self._display = float(value)
        self.value_changed.emit(self._display)

    def _on_finished(self) -> None:
        if self._target is None:
            return
        self._display = float(self._target)
        self.value_changed.emit(self._display)

    def finish(self) -> None:
        """Settle a hidden view without leaving an animation timer running."""
        self._anim.stop()
        self._on_finished()


def percent_color(percent: Optional[int]) -> str:
    if percent is None:
        return "#D0D5DD"
    if percent >= 50:
        return "#7DDEA0"
    if percent >= 20:
        return "#F6D56B"
    return "#FF8A80"


def apply_text_shadow(widget: QWidget) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(12)
    effect.setOffset(0, 1)
    effect.setColor(QColor(0, 0, 0, 210))
    widget.setGraphicsEffect(effect)


class QuotaStyleWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)
        self._state = QuotaState.empty()

    def apply(self, state: QuotaState) -> None:
        self._state = state
        self._render(state)
        self.update()

    def preferred_size(self) -> QSize:
        return QSize(280, 120)

    def minimum_size(self) -> QSize:
        return QSize(180, 88)

    def set_show_five(self, visible: bool) -> None:
        widget = getattr(self, "_five", None)
        if widget is not None:
            widget.setVisible(visible)

    def hover_text(self) -> str:
        return ""

    def _render(self, state: QuotaState) -> None:
        raise NotImplementedError


class ClassicStyle(QuotaStyleWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._five = _BarRow(QUOTA_FIVE_LABEL)
        self._week = _BarRow(QUOTA_WEEK_LABEL)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._five, 1)
        layout.addWidget(self._week, 1)

    def preferred_size(self) -> QSize:
        return QSize(280, 128)

    def minimum_size(self) -> QSize:
        return QSize(200, 100)

    def _render(self, state: QuotaState) -> None:
        self._five.apply(state.five_hour)
        self._week.apply(state.week)


class RingsStyle(QuotaStyleWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._five = _RingCard(QUOTA_FIVE_LABEL)
        self._week = _RingCard(QUOTA_WEEK_LABEL)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._five, 1)
        layout.addWidget(self._week, 1)

    def preferred_size(self) -> QSize:
        return QSize(300, 150)

    def minimum_size(self) -> QSize:
        return QSize(220, 120)

    def _render(self, state: QuotaState) -> None:
        self._five.apply(state.five_hour)
        self._week.apply(state.week)


class TilesStyle(QuotaStyleWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._five = _TileCard(QUOTA_FIVE_LABEL)
        self._week = _TileCard(QUOTA_WEEK_LABEL)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._five, 1)
        layout.addWidget(self._week, 1)

    def preferred_size(self) -> QSize:
        return QSize(320, 132)

    def minimum_size(self) -> QSize:
        return QSize(240, 108)

    def _render(self, state: QuotaState) -> None:
        self._five.apply(state.five_hour)
        self._week.apply(state.week)


class CompactStyle(QuotaStyleWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._five = _CompactItem(QUOTA_FIVE_LABEL)
        self._week = _CompactItem(QUOTA_WEEK_LABEL)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._five, 1)
        layout.addWidget(self._week, 1)

    def preferred_size(self) -> QSize:
        return QSize(300, 56)

    def minimum_size(self) -> QSize:
        return QSize(220, 44)

    def _render(self, state: QuotaState) -> None:
        self._five.apply(state.five_hour)
        self._week.apply(state.week)


class MinimalStyle(QuotaStyleWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._five_value = _ShadowLabel("N/A")
        self._week_value = _ShadowLabel("N/A")
        self._five_meta = _ShadowLabel(QUOTA_FIVE_LABEL)
        self._week_meta = _ShadowLabel(QUOTA_WEEK_LABEL)
        self._five_anim = PercentAnimator(self)
        self._week_anim = PercentAnimator(self)
        self._five_anim.value_changed.connect(lambda value: self._set_value(self._five_value, value))
        self._week_anim.value_changed.connect(lambda value: self._set_value(self._week_value, value))
        self._five_value.setObjectName("heroPercent")
        self._week_value.setObjectName("heroPercent")
        self._five_meta.setObjectName("mutedLabel")
        self._week_meta.setObjectName("mutedLabel")
        self._five_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._week_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._five_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._week_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._five_box = QWidget()
        self._five_box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        left = QVBoxLayout(self._five_box)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        left.addStretch(1)
        left.addWidget(self._five_value)
        left.addWidget(self._five_meta)
        left.addStretch(1)
        self._week_box = QWidget()
        self._week_box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        right = QVBoxLayout(self._week_box)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)
        right.addStretch(1)
        right.addWidget(self._week_value)
        right.addWidget(self._week_meta)
        right.addStretch(1)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._five_box, 1)
        layout.addWidget(self._week_box, 1)

    def preferred_size(self) -> QSize:
        return QSize(240, 72)

    def minimum_size(self) -> QSize:
        return QSize(180, 56)

    def set_show_five(self, visible: bool) -> None:
        self._five_box.setVisible(visible)

    def _render(self, state: QuotaState) -> None:
        self._apply_side(self._five_anim, self._five_value, self._five_meta, QUOTA_FIVE_LABEL, state.five_hour)
        self._apply_side(self._week_anim, self._week_value, self._week_meta, QUOTA_WEEK_LABEL, state.week)

    def _apply_side(
        self,
        animator: PercentAnimator,
        value: QLabel,
        meta: QLabel,
        title: str,
        view: WindowView,
    ) -> None:
        if view.remaining_percent is None:
            value.setStyleSheet("color: #D0D5DD;")
            meta.setText(title)
            animator.set_percent(None)
            return
        value.setStyleSheet("color: %s;" % percent_color(view.remaining_percent))
        meta.setText("%s · %s" % (title, format_reset_time(view.resets_at)))
        animator.set_percent(view.remaining_percent)

    def _set_value(self, label: QLabel, value: Optional[float]) -> None:
        label.setText(format_percent_label(value))


class OrbStyle(QuotaStyleWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._week_only = False
        self._title = QUOTA_FIVE_LABEL
        self._view = WindowView.unavailable()
        self._display: Optional[float] = None
        self._animator = PercentAnimator(self, duration=720)
        self._animator.value_changed.connect(self._on_animated)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self.setMinimumSize(40, 40)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._timer.stop()
        super().hideEvent(event)

    def preferred_size(self) -> QSize:
        return QSize(*STYLE_WINDOW_SIZES[ORB_STYLE])

    def minimum_size(self) -> QSize:
        return QSize(ORB_MIN_SIZE, ORB_MIN_SIZE)

    def set_show_five(self, visible: bool) -> None:
        self._week_only = not visible
        self._render(self._state)

    def hover_text(self) -> str:
        return quota_hover_text(self._title, self._view)

    def _render(self, state: QuotaState) -> None:
        self._title, self._view = resolve_orb_quota(state, week_only=self._week_only)
        self._animator.set_percent(self._view.remaining_percent)
        self.update()

    def _on_animated(self, value: Optional[float]) -> None:
        self._display = value
        self.update()

    def _now(self) -> float:
        return self._clock.elapsed() / 1000.0

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0)
        side = max(28.0, min(box.width(), box.height()))
        ball = QRectF(
            box.center().x() - side / 2.0,
            box.center().y() - side / 2.0,
            side,
            side,
        )
        clip = QPainterPath()
        clip.addEllipse(ball)
        painter.setClipPath(clip)
        painter.setPen(Qt.PenStyle.NoPen)

        cavity = QRadialGradient(ball.center(), side * 0.56)
        cavity.setColorAt(0.0, QColor(58, 72, 84, 210))
        cavity.setColorAt(0.7, QColor(32, 42, 52, 230))
        cavity.setColorAt(1.0, QColor(20, 28, 36, 240))
        painter.setBrush(cavity)
        painter.drawEllipse(ball)

        percent = self._view.remaining_percent
        shown = self._display if self._display is not None else (
            None if percent is None else float(percent)
        )
        if shown is not None:
            self._paint_water(painter, ball, side, shown)

        sheen = QRadialGradient(
            ball.center().x() - side * 0.18,
            ball.center().y() - side * 0.22,
            side * 0.36,
        )
        sheen.setColorAt(0.0, QColor(255, 255, 255, 52))
        sheen.setColorAt(0.4, QColor(255, 255, 255, 14))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(sheen)
        painter.drawEllipse(ball)

        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(230, 240, 246, 50), max(1.0, side * 0.014)))
        painter.drawEllipse(ball.adjusted(0.8, 0.8, -0.8, -0.8))
        painter.setPen(QPen(QColor(255, 255, 255, 70), max(1.0, side * 0.012)))
        painter.drawArc(
            ball.adjusted(side * 0.16, side * 0.09, -side * 0.38, -side * 0.52),
            62 * 16,
            68 * 16,
        )

        text = format_percent_label(shown)
        font = display_font(max(18, int(side * 0.26)))
        painter.setFont(font)
        painter.setPen(QColor(8, 20, 16, 110))
        painter.drawText(ball.translated(0, 1.2), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor(248, 255, 252, 240))
        painter.drawText(ball, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

    def _paint_water(self, painter: QPainter, ball: QRectF, side: float, shown: float) -> None:
        t = self._now()
        level = max(0.1, min(0.88, shown / 100.0))
        breath = math.sin(t * 1.15) * (side * 0.012)
        base_y = ball.bottom() - ball.height() * level + breath
        amp = max(4.2, side * 0.055)
        wave = self._wave_path(ball, base_y, t, amp)
        pulse = 0.5 + 0.5 * math.sin(t * 1.4)
        fill = QLinearGradient(ball.left(), base_y - amp * 2.0, ball.left(), ball.bottom())
        fill.setColorAt(0.0, QColor(ORB_WATER.red(), ORB_WATER.green(), ORB_WATER.blue(), 220))
        fill.setColorAt(0.28, QColor(ORB_WATER_MID.red(), ORB_WATER_MID.green(), ORB_WATER_MID.blue(), 230))
        fill.setColorAt(1.0, QColor(ORB_WATER_DEEP.red(), ORB_WATER_DEEP.green(), ORB_WATER_DEEP.blue(), 235))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPath(wave)
        self._paint_swell(painter, ball, base_y, side, t, pulse)
        self._paint_surface(painter, ball, base_y, t, amp, side)

    def _wave_path(self, ball: QRectF, base_y: float, t: float, amp: float) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(ball.left() - 4.0, ball.bottom() + 4.0)
        path.lineTo(ball.left() - 4.0, self._surface_y(ball, ball.left(), base_y, t, amp))
        steps = 48
        for index in range(steps + 1):
            x = ball.left() + ball.width() * index / steps
            path.lineTo(x, self._surface_y(ball, x, base_y, t, amp))
        path.lineTo(ball.right() + 4.0, ball.bottom() + 4.0)
        path.closeSubpath()
        return path

    def _surface_y(self, ball: QRectF, x: float, base_y: float, t: float, amp: float) -> float:
        nx = (x - ball.left()) / max(1.0, ball.width())
        return (
            base_y
            + math.sin(t * 2.35 + nx * math.pi * 1.8) * amp
            + math.sin(t * 1.45 + nx * math.pi * 0.95 + 1.3) * amp * 0.55
            + math.sin(t * 3.1 + nx * math.pi * 3.2 + 0.4) * amp * 0.18
        )

    def _paint_surface(
        self,
        painter: QPainter,
        ball: QRectF,
        base_y: float,
        t: float,
        amp: float,
        side: float,
    ) -> None:
        ridge = QPainterPath()
        steps = 48
        for index in range(steps + 1):
            x = ball.left() + ball.width() * index / steps
            y = self._surface_y(ball, x, base_y, t, amp)
            if index == 0:
                ridge.moveTo(x, y)
            else:
                ridge.lineTo(x, y)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(12, 70, 48, 90), max(1.2, side * 0.018)))
        painter.drawPath(ridge.translated(0, 1.4))
        highlight = QPen(QColor(255, 255, 255, 150), max(1.6, side * 0.022))
        highlight.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(highlight)
        painter.drawPath(ridge)

    def _paint_swell(
        self,
        painter: QPainter,
        ball: QRectF,
        base_y: float,
        side: float,
        t: float,
        pulse: float,
    ) -> None:
        depth = max(10.0, ball.bottom() - base_y)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, scale in enumerate((0.22, 0.48, 0.72)):
            y = base_y + depth * (0.18 + scale * 0.55) + math.sin(t * 1.7 + index) * (side * 0.03)
            band = QRectF(ball.left() - 4.0, y - side * 0.06, ball.width() + 8.0, side * 0.12)
            glow = QLinearGradient(band.left(), band.top(), band.left(), band.bottom())
            alpha = int(36 + 28 * pulse - index * 6)
            glow.setColorAt(0.0, QColor(210, 255, 230, 0))
            glow.setColorAt(0.5, QColor(190, 255, 220, max(18, alpha)))
            glow.setColorAt(1.0, QColor(210, 255, 230, 0))
            painter.setBrush(glow)
            painter.drawEllipse(band)
        drift = math.sin(t * 1.05) * (side * 0.16)
        spot = QRectF(
            ball.center().x() - side * 0.16 + drift,
            base_y + depth * 0.28,
            side * 0.28,
            side * 0.2,
        )
        spark = QRadialGradient(spot.center(), side * 0.16)
        spark.setColorAt(0.0, QColor(255, 255, 245, int(50 + 24 * pulse)))
        spark.setColorAt(1.0, QColor(255, 255, 245, 0))
        painter.setBrush(spark)
        painter.drawEllipse(spot)


def create_style_widget(name: str, parent: Optional[QWidget] = None) -> QuotaStyleWidget:
    constructors = {
        "classic": ClassicStyle, "rings": RingsStyle, "tiles": TilesStyle,
        "compact": CompactStyle, "minimal": MinimalStyle, ORB_STYLE: OrbStyle,
    }
    return constructors[name](parent)


def create_style_widgets(parent: Optional[QWidget] = None) -> Dict[str, QuotaStyleWidget]:
    return {name: create_style_widget(name, parent) for name in STYLE_LABELS}


class _ShadowLabel(QLabel):
    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_text_shadow(self)


class _BarRow(QWidget):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._title = _ShadowLabel(title)
        self._title.setObjectName("rowTitle")
        self._percent = _ShadowLabel("N/A")
        self._percent.setObjectName("rowPercent")
        self._reset = _ShadowLabel("重置 N/A")
        self._reset.setObjectName("rowReset")
        self._bar = _QuotaBar()
        self._animator = PercentAnimator(self)
        self._color_percent: Optional[int] = None
        self._animator.value_changed.connect(self._on_animated)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._percent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self._bar)
        layout.addWidget(self._reset)

    def apply(self, view: WindowView) -> None:
        if view.remaining_percent is None:
            self._color_percent = None
            self._percent.setStyleSheet("color: #D0D5DD;")
            self._reset.setText("重置 N/A")
            self._animator.set_percent(None)
            return
        self._color_percent = view.remaining_percent
        self._percent.setStyleSheet("color: %s;" % percent_color(view.remaining_percent))
        self._reset.setText("重置 %s" % format_reset_time(view.resets_at))
        self._animator.set_percent(view.remaining_percent)

    def _on_animated(self, value: Optional[float]) -> None:
        self._percent.setText(format_percent_label(value))
        self._bar.set_display(value, self._color_percent)


class _QuotaBar(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._display: Optional[float] = None
        self._color_percent: Optional[int] = None
        self.setMinimumHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(8)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_percent(self, percent: Optional[int]) -> None:
        self.set_display(None if percent is None else float(percent), percent)

    def set_display(self, value: Optional[float], color_percent: Optional[int] = None) -> None:
        self._display = value
        self._color_percent = color_percent if color_percent is not None else (
            None if value is None else int(round(value))
        )
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.rect().adjusted(0, 1, 0, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 48))
        painter.drawRoundedRect(track, 4, 4)
        if self._display is None:
            painter.end()
            return
        width = max(6, int(track.width() * max(0.0, min(100.0, self._display)) / 100.0))
        fill = QRect(track.left(), track.top(), width, track.height())
        painter.setBrush(QColor(percent_color(self._color_percent)))
        painter.drawRoundedRect(fill, 4, 4)
        painter.end()


class _RingCard(QWidget):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title = title
        self._view = WindowView.unavailable()
        self._display: Optional[float] = None
        self._animator = PercentAnimator(self)
        self._animator.value_changed.connect(self._on_animated)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMinimumSize(100, 110)

    def apply(self, view: WindowView) -> None:
        self._view = view
        self._animator.set_percent(view.remaining_percent)

    def _on_animated(self, value: Optional[float]) -> None:
        self._display = value
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        percent = self._view.remaining_percent
        shown = self._display if self._display is not None else percent
        color = QColor(percent_color(percent))
        box = self.rect()
        ring = min(box.width(), int(box.height() * 0.72))
        ring = max(64, ring)
        ring_rect = QRectF(
            box.center().x() - ring / 2.0,
            box.top() + 2,
            ring,
            ring,
        )
        pen_width = max(6.0, ring * 0.11)
        track = QPen(QColor(255, 255, 255, 46), pen_width)
        track.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = pen_width / 2.0 + 1
        arc = ring_rect.adjusted(inset, inset, -inset, -inset)
        painter.drawArc(arc, 0, 360 * 16)
        if shown is not None:
            progress = QPen(color, pen_width)
            progress.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(progress)
            painter.drawArc(arc, 90 * 16, int(-360 * 16 * max(0.0, min(100.0, shown)) / 100.0))
        text = "N/A" if shown is None else "%d" % int(round(shown))
        painter.setPen(QColor(255, 255, 255, 235))
        font = display_font(max(18, int(ring * 0.30)), bold=True)
        painter.setFont(font)
        painter.drawText(arc, Qt.AlignmentFlag.AlignCenter, text)
        meta = QRect(box.left(), int(ring_rect.bottom()) - 2, box.width(), box.bottom() - int(ring_rect.bottom()) + 6)
        painter.setPen(QColor(230, 234, 240, 220))
        small = display_font(max(12, int(box.height() * 0.10)))
        painter.setFont(small)
        reset = format_reset_time(self._view.resets_at) if percent is not None else "N/A"
        painter.drawText(meta, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "%s  %s" % (self._title, reset))
        painter.end()


class _TileCard(QWidget):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._title = title
        self._view = WindowView.unavailable()
        self._display: Optional[float] = None
        self._animator = PercentAnimator(self)
        self._animator.value_changed.connect(self._on_animated)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMinimumSize(110, 96)

    def apply(self, view: WindowView) -> None:
        self._view = view
        self._animator.set_percent(view.remaining_percent)

    def _on_animated(self, value: Optional[float]) -> None:
        self._display = value
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(255, 255, 255, 36), 1))
        painter.setBrush(QColor(255, 255, 255, 18))
        painter.drawRoundedRect(rect, 14, 14)
        percent = self._view.remaining_percent
        shown = self._display if self._display is not None else percent
        color = QColor(percent_color(percent))
        painter.setPen(color)
        font = display_font(max(24, int(rect.height() * 0.40)), bold=True)
        painter.setFont(font)
        value = format_percent_label(shown)
        painter.drawText(rect.adjusted(8, 6, -8, int(-rect.height() * 0.42)), Qt.AlignmentFlag.AlignCenter, value)
        painter.setPen(QColor(236, 239, 244, 230))
        small = display_font(max(12, int(rect.height() * 0.13)))
        painter.setFont(small)
        reset = format_reset_time(self._view.resets_at) if percent is not None else "N/A"
        painter.drawText(
            rect.adjusted(8, int(rect.height() * 0.58), -8, -8),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "%s\n%s" % (self._title, reset),
        )
        painter.end()


class _CompactItem(QWidget):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._title = _ShadowLabel(title)
        self._title.setObjectName("rowTitle")
        self._percent = _ShadowLabel("N/A")
        self._percent.setObjectName("rowPercent")
        self._reset = _ShadowLabel("")
        self._reset.setObjectName("rowReset")
        self._bar = _QuotaBar()
        self._animator = PercentAnimator(self)
        self._color_percent: Optional[int] = None
        self._animator.value_changed.connect(self._on_animated)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._title)
        top.addStretch(1)
        top.addWidget(self._percent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addLayout(top)
        layout.addWidget(self._bar)
        layout.addWidget(self._reset)

    def apply(self, view: WindowView) -> None:
        if view.remaining_percent is None:
            self._color_percent = None
            self._percent.setStyleSheet("color: #D0D5DD;")
            self._reset.setText("重置 N/A")
            self._animator.set_percent(None)
            return
        self._color_percent = view.remaining_percent
        self._percent.setStyleSheet("color: %s;" % percent_color(view.remaining_percent))
        self._reset.setText(format_reset_time(view.resets_at))
        self._animator.set_percent(view.remaining_percent)

    def _on_animated(self, value: Optional[float]) -> None:
        self._percent.setText(format_percent_label(value))
        self._bar.set_display(value, self._color_percent)


def style_preferred_window_size(style_name: str) -> Tuple[int, int]:
    return STYLE_WINDOW_SIZES.get(style_name, STYLE_WINDOW_SIZES["classic"])
