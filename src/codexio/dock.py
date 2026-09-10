from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QBoxLayout, QLabel, QSizePolicy, QToolButton, QWidget

from codexio.rate_limits import QuotaState, WindowView
from codexio.settings import DEFAULT_DOCK_EDGE, DOCK_EDGES
from codexio.visuals import (
    QUOTA_FIVE_LABEL,
    QUOTA_WEEK_LABEL,
    PercentAnimator,
    apply_text_shadow,
    display_font,
    format_percent_label,
    percent_color,
    quota_hover_text,
)

DOCK_NONE = "none"
DOCK_TOP = "top"
DOCK_BOTTOM = "bottom"
DOCK_LEFT = "left"
DOCK_RIGHT = "right"
DOCKED_EDGES = (DOCK_TOP, DOCK_BOTTOM, DOCK_LEFT, DOCK_RIGHT)
SNAP_DISTANCE = 24
DOCK_TOP_SIZE = (642, 40)
DOCK_SIDE_SIZE = (52, 252)
DOCK_TOP_WEEK_SIZE = (432, 40)
DOCK_SIDE_WEEK_SIZE = (52, 172)
DOCK_TOP_MIN_SIZE = (460, 40)
DOCK_TOP_WEEK_MIN_SIZE = (300, 40)
DOCK_SIDE_MIN_SIZE = (52, 220)
DOCK_SIDE_WEEK_MIN_SIZE = (52, 152)
DOCK_VERTICAL_BAR_REVEAL = 118
DOCK_TOP_MAX_SIZE = (960, 96)
DOCK_SIDE_MAX_SIZE = (120, 720)
DOCK_BAR_MIN_LENGTH = 48
DOCK_SCALE_MAX = 2.2
DOCK_BAR_COLOR = QColor("#3DDC97")
DOCK_BAR_TRACK = QColor(86, 96, 110, 235)
DOCK_BAR_EDGE = QColor(16, 18, 22, 220)


def normalize_dock_edge(value: object) -> str:
    if isinstance(value, str) and value in DOCK_EDGES:
        return value
    return DEFAULT_DOCK_EDGE


def choose_dock_edge(geom: QRect, screen: QRect, threshold: int = SNAP_DISTANCE) -> str:
    if geom.isEmpty() or screen.isEmpty():
        return DOCK_NONE
    limit = SNAP_DISTANCE if threshold is None else threshold
    top = geom.top() - screen.top()
    bottom = screen.bottom() - geom.bottom()
    left = geom.left() - screen.left()
    right = screen.right() - geom.right()
    candidates = []
    if top <= limit:
        candidates.append((max(0, top), 0, DOCK_TOP))
    if bottom <= limit:
        candidates.append((max(0, bottom), 0, DOCK_BOTTOM))
    if left <= limit:
        candidates.append((max(0, left), 1, DOCK_LEFT))
    if right <= limit:
        candidates.append((max(0, right), 1, DOCK_RIGHT))
    if not candidates:
        return DOCK_NONE
    candidates.sort()
    return candidates[0][2]


def is_horizontal_dock(edge: str) -> bool:
    return edge in (DOCK_TOP, DOCK_BOTTOM)


def is_vertical_dock(edge: str) -> bool:
    return edge in (DOCK_LEFT, DOCK_RIGHT)


def dock_size(edge: str, week_only: bool = False) -> Tuple[int, int]:
    if is_horizontal_dock(edge):
        return DOCK_TOP_WEEK_SIZE if week_only else DOCK_TOP_SIZE
    if is_vertical_dock(edge):
        return DOCK_SIDE_WEEK_SIZE if week_only else DOCK_SIDE_SIZE
    return (0, 0)


def dock_min_size(edge: str, week_only: bool = False) -> Tuple[int, int]:
    if is_horizontal_dock(edge):
        return DOCK_TOP_WEEK_MIN_SIZE if week_only else DOCK_TOP_MIN_SIZE
    if is_vertical_dock(edge):
        return DOCK_SIDE_WEEK_MIN_SIZE if week_only else DOCK_SIDE_MIN_SIZE
    return (0, 0)


def dock_max_size(edge: str) -> Tuple[int, int]:
    if is_horizontal_dock(edge):
        return DOCK_TOP_MAX_SIZE
    if is_vertical_dock(edge):
        return DOCK_SIDE_MAX_SIZE
    return (0, 0)


def dock_content_scale(edge: str, width: int, height: int) -> float:
    if is_vertical_dock(edge):
        return _clamp_scale(width / float(DOCK_SIDE_SIZE[0]))
    if is_horizontal_dock(edge):
        return _clamp_scale(height / float(DOCK_TOP_SIZE[1]))
    return 1.0


def _clamp_scale(value: float) -> float:
    return max(1.0, min(DOCK_SCALE_MAX, value))


def clamp_dock_size(edge: str, width: int, height: int, week_only: bool = False) -> Tuple[int, int]:
    min_w, min_h = dock_min_size(edge, week_only)
    max_w, max_h = dock_max_size(edge)
    if min_w <= 0:
        return (width, height)
    return (max(min_w, min(max_w, width)), max(min_h, min(max_h, height)))


def snap_geometry(
    edge: str,
    geom: QRect,
    screen: QRect,
    week_only: bool = False,
    size: Optional[Tuple[int, int]] = None,
) -> QRect:
    if edge == DOCK_NONE or screen.isEmpty():
        return QRect(geom)
    if size is None:
        width, height = dock_size(edge, week_only)
    else:
        width, height = clamp_dock_size(edge, int(size[0]), int(size[1]), week_only)
    if is_horizontal_dock(edge):
        width = min(width, max(1, screen.width()))
        x = _along_axis(geom.center().x() - width // 2, screen.left(), screen.right() - width + 1)
        if edge == DOCK_TOP:
            return QRect(x, screen.top(), width, height)
        return QRect(x, screen.bottom() - height + 1, width, height)
    height = min(height, max(1, screen.height()))
    y = _along_axis(geom.center().y() - height // 2, screen.top(), screen.bottom() - height + 1)
    if edge == DOCK_LEFT:
        return QRect(screen.left(), y, width, height)
    return QRect(screen.right() - width + 1, y, width, height)


def pin_docked_geometry(edge: str, geom: QRect, screen: QRect) -> QRect:
    if edge == DOCK_NONE or screen.isEmpty() or geom.isEmpty():
        return QRect(geom)
    if is_horizontal_dock(edge):
        x = _along_axis(geom.left(), screen.left(), screen.right() - geom.width() + 1)
        if edge == DOCK_TOP:
            return QRect(x, screen.top(), geom.width(), geom.height())
        return QRect(x, screen.bottom() - geom.height() + 1, geom.width(), geom.height())
    y = _along_axis(geom.top(), screen.top(), screen.bottom() - geom.height() + 1)
    if edge == DOCK_LEFT:
        return QRect(screen.left(), y, geom.width(), geom.height())
    return QRect(screen.right() - geom.width() + 1, y, geom.width(), geom.height())


def float_geometry(geom: QRect, screen: QRect, size: Tuple[int, int]) -> QRect:
    width, height = size
    if screen.isEmpty():
        return QRect(geom.x(), geom.y(), width, height)
    x = _along_axis(geom.x(), screen.left(), screen.right() - width + 1)
    y = _along_axis(geom.y(), screen.top(), screen.bottom() - height + 1)
    return QRect(x, y, width, height)


def _along_axis(value: int, low: int, high: int) -> int:
    if high < low:
        return low
    return max(low, min(high, value))


def snap_threshold(docked: bool) -> int:
    return 40 if docked else SNAP_DISTANCE


class SnapPreview(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("snapPreview")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._edge = DOCK_NONE
        self.hide()

    @property
    def edge(self) -> str:
        return self._edge

    def show_at(self, geom: QRect, edge: str) -> None:
        self._edge = edge
        if self.geometry() != geom:
            self.setGeometry(geom)
        if not self.isVisible():
            self.show()
        self.update()

    def hide_preview(self) -> None:
        self._edge = DOCK_NONE
        if self.isVisible():
            self.hide()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -3, -3)
        painter.setBrush(QColor(120, 168, 255, 80))
        painter.setPen(QPen(QColor(168, 204, 255, 230), 2))
        painter.drawRoundedRect(rect, 10, 10)
        painter.end()


class DockStrip(QWidget):
    def __init__(self, host, on_refresh, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._host = host
        self.setObjectName("dockStrip")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("拖动可沿边缘移动或拖出停靠")
        self._edge = DOCK_TOP
        self._week_only = False
        self._grip = _DockGrip(self)
        self._five = _DockQuota(QUOTA_FIVE_LABEL)
        self._week = _DockQuota(QUOTA_WEEK_LABEL)
        self._refresh = QToolButton()
        self._refresh.setObjectName("refreshButton")
        self._refresh.setFont(display_font(13))
        self._refresh.setText("刷新")
        self._refresh.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._refresh.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._refresh.clicked.connect(on_refresh)
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._scale = 1.0
        self._group_gap = 20
        self.set_edge(DOCK_TOP)

    @property
    def refresh_button(self) -> QToolButton:
        return self._refresh

    def set_edge(self, edge: str) -> None:
        self._edge = edge if edge in DOCKED_EDGES else DOCK_TOP
        vertical = is_vertical_dock(self._edge)
        self._layout.setDirection(
            QBoxLayout.Direction.TopToBottom if vertical else QBoxLayout.Direction.LeftToRight
        )
        self._layout.setSpacing(10 if vertical else 16)
        self._grip.set_vertical(vertical)
        self._five.set_vertical(vertical)
        self._week.set_vertical(vertical)
        self._refresh.setText("刷" if vertical else "刷新")
        self._rebuild_layout(vertical)
        self._apply_metrics()

    def set_week_only(self, week_only: bool) -> None:
        self._week_only = bool(week_only)
        self._five.setVisible(not self._week_only)
        self._rebuild_layout(is_vertical_dock(self._edge))
        self._apply_metrics()

    def _rebuild_layout(self, vertical: bool) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            del item
        if vertical:
            self._layout.addWidget(self._grip, 0, Qt.AlignmentFlag.AlignHCenter)
            if not self._week_only:
                self._layout.addWidget(self._five, 1)
            self._layout.addWidget(self._week, 1)
            self._layout.addWidget(self._refresh, 0, Qt.AlignmentFlag.AlignHCenter)
            return
        self._layout.addWidget(self._grip, 0, Qt.AlignmentFlag.AlignCenter)
        if not self._week_only:
            self._layout.addWidget(self._five, 1)
            self._layout.addSpacing(self._group_gap)
        self._layout.addWidget(self._week, 1)
        self._layout.addWidget(self._refresh, 0, Qt.AlignmentFlag.AlignCenter)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_metrics()

    def _apply_metrics(self) -> None:
        host = self._host
        width = host.width() if host is not None else self.width()
        height = host.height() if host is not None else self.height()
        if width <= 1 or height <= 1:
            return
        desired = dock_content_scale(self._edge, width, height)
        self._apply_scale(desired)
        fitted = self._fitted_scale(desired)
        if fitted + 0.03 < desired:
            self._apply_scale(fitted)
            self._scale = fitted
            return
        self._scale = desired

    def _fitted_scale(self, scale: float) -> float:
        if scale <= 1.01:
            return 1.0
        vertical = is_vertical_dock(self._edge)
        available = self.width() if not vertical else self.height()
        if available <= 1:
            return scale
        hint = self.minimumSizeHint()
        needed = hint.width() if not vertical else hint.height()
        if needed <= available + 6:
            return scale
        return max(1.0, scale * available / float(needed))

    def _apply_scale(self, scale: float) -> None:
        vertical = is_vertical_dock(self._edge)
        self._layout.setSpacing(max(6, int(round((10 if vertical else 16) * scale))))
        self._group_gap = max(10, int(round(20 * scale)))
        self._sync_group_gap()
        self._grip.apply_metrics(scale)
        self._five.apply_metrics(scale)
        self._week.apply_metrics(scale)
        refresh_px = max(11, int(round(13 * scale)))
        self._refresh.setFont(display_font(refresh_px))
        self._refresh.setStyleSheet("font-size: %dpx;" % refresh_px)
        self._refresh.adjustSize()

    def _sync_group_gap(self) -> None:
        if is_vertical_dock(self._edge):
            return
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            spacer = item.spacerItem() if item is not None else None
            if spacer is None:
                continue
            spacer.changeSize(self._group_gap, 0)
            self._layout.invalidate()
            return

    def apply(self, state: QuotaState) -> None:
        self._five.apply(state.five_hour)
        self._week.apply(state.week)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._host._handle_mouse_press(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        self._host._handle_mouse_move(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._host._handle_mouse_release(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self._host._handle_mouse_double_click(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        self._host._show_context_menu(event.globalPos())


class _DockQuota(QWidget):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._plain_title = title
        self._view = WindowView.unavailable()
        self._title = QLabel(title)
        self._title.setObjectName("dockTitle")
        self._title.setFont(display_font(13))
        self._title.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_text_shadow(self._title)
        self._percent = QLabel("N/A")
        self._percent.setObjectName("dockPercent")
        self._percent.setFont(display_font(16, bold=True))
        self._percent.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._percent.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_text_shadow(self._percent)
        self._vertical = False
        self._bar = _DockBar()
        self._animator = PercentAnimator(self)
        self._color_percent: Optional[int] = None
        self._animator.value_changed.connect(self._on_animated)
        self._text = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._text.setContentsMargins(0, 0, 0, 0)
        self._text.setSpacing(4)
        self._text.addWidget(self._title)
        self._text.addWidget(self._percent)
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(8)
        self._box.addLayout(self._text, 0)
        self._box.addWidget(self._bar, 1)
        self.set_vertical(False)

    def set_vertical(self, vertical: bool) -> None:
        self._vertical = vertical
        direction = (
            QBoxLayout.Direction.TopToBottom if vertical else QBoxLayout.Direction.LeftToRight
        )
        self._text.setDirection(direction)
        self._box.setDirection(direction)
        self._text.setSpacing(4)
        self._box.setSpacing(4 if vertical else 8)
        self._bar.set_vertical(vertical)
        self._title.setWordWrap(False)
        parts = self._plain_title.split(" ", 1)
        if vertical and len(parts) == 2:
            self._title.setText("%s\n%s" % (parts[0], parts[1]))
        else:
            self._title.setText(self._plain_title)
        if vertical:
            self._clear_text_width_locks()
            self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            self.setMinimumHeight(58)
            self.setMinimumWidth(0)
            self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self._percent.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self._box.setAlignment(self._bar, Qt.AlignmentFlag.AlignHCenter)
        else:
            self._lock_horizontal_text()
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.setMinimumHeight(0)
            self.setMinimumWidth(self.sizeHint().width())
            self._title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._percent.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._box.setAlignment(self._bar, Qt.AlignmentFlag.AlignVCenter)
        self.apply_metrics(1.0)

    def apply_metrics(self, scale: float) -> None:
        title_px = max(11, int(round(13 * scale)))
        percent_px = max(13, int(round(16 * scale)))
        self._title.setFont(display_font(title_px))
        self._percent.setFont(display_font(percent_px, bold=True))
        self._text.setSpacing(max(2, int(round(4 * scale))))
        self._box.setSpacing(max(3, int(round((4 if self._vertical else 8) * scale))))
        self._bar.apply_metrics(scale, self._vertical)
        if self._vertical:
            self._clear_text_width_locks()
            self.setMinimumHeight(max(48, int(round(58 * scale))))
            self.setMinimumWidth(0)
        else:
            self._lock_horizontal_text()
            self.setMinimumHeight(0)
            self.setMinimumWidth(self.sizeHint().width())
        self._sync_bar_visible()

    def _clear_text_width_locks(self) -> None:
        for label in (self._title, self._percent):
            label.setMinimumSize(0, 0)
            label.setMaximumSize(16777215, 16777215)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def _lock_horizontal_text(self) -> None:
        self._title.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._percent.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._title.setMinimumWidth(0)
        self._title.setMaximumWidth(16777215)
        self._title.setFixedWidth(max(1, self._title.sizeHint().width() + 2))
        reserved = max(
            self._percent.fontMetrics().horizontalAdvance("100%"),
            self._percent.fontMetrics().horizontalAdvance("N/A"),
            1,
        )
        self._percent.setFixedWidth(reserved + 2)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_bar_visible()

    def _sync_bar_visible(self) -> None:
        show = (not self._vertical) or self.height() >= DOCK_VERTICAL_BAR_REVEAL
        if self._bar.isVisible() != show:
            self._bar.setVisible(show)
            self.updateGeometry()

    def apply(self, view: WindowView) -> None:
        self._view = view
        self.setToolTip(quota_hover_text(self._plain_title, view))
        if view.remaining_percent is None:
            self._color_percent = None
            self._percent.setStyleSheet("color: #D0D5DD;")
            self._animator.set_percent(None)
            return
        self._color_percent = view.remaining_percent
        self._percent.setStyleSheet("color: %s;" % percent_color(view.remaining_percent))
        self._animator.set_percent(view.remaining_percent)

    def hover_text(self) -> str:
        return quota_hover_text(self._plain_title, self._view)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._forward("_handle_mouse_press", event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        self._forward("_handle_mouse_move", event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._forward("_handle_mouse_release", event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self._forward("_handle_mouse_double_click", event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        host = self._host()
        if host is not None:
            host._show_context_menu(event.globalPos())

    def _host(self):
        parent = self.parentWidget()
        return getattr(parent, "_host", None)

    def _forward(self, name: str, event) -> None:
        host = self._host()
        if host is not None:
            getattr(host, name)(event)

    def _on_animated(self, value: Optional[float]) -> None:
        self._percent.setText(format_percent_label(value))
        if value is None:
            self._percent.setStyleSheet("color: #D0D5DD;")
        self._bar.set_display(value)


class _DockBar(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._percent: Optional[float] = None
        self._vertical = False
        self._thickness = 8
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.set_vertical(False)

    def set_vertical(self, vertical: bool) -> None:
        self.apply_metrics(1.0, vertical)

    def apply_metrics(self, scale: float, vertical: bool) -> None:
        self._vertical = vertical
        self._thickness = max(6, int(round(8 * scale)))
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if vertical:
            self.setFixedWidth(self._thickness)
            self.setMinimumHeight(max(20, int(round(28 * scale))))
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(self._thickness)
            self.setMinimumWidth(max(36, int(round(DOCK_BAR_MIN_LENGTH * scale))))
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.update()

    def set_percent(self, percent: Optional[int]) -> None:
        self.set_display(None if percent is None else float(percent))

    def set_display(self, percent: Optional[float]) -> None:
        self._percent = percent
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.rect()
        painter.setPen(Qt.PenStyle.NoPen)
        radius = max(2.0, self._thickness / 2.0)
        painter.setBrush(DOCK_BAR_TRACK)
        painter.drawRoundedRect(track, radius, radius)
        if self._percent is not None:
            ratio = max(0.0, min(100.0, self._percent)) / 100.0
            if self._vertical:
                height = max(6 if self._percent > 0 else 0, int(round(track.height() * ratio)))
                fill = QRect(track.left(), track.top() + track.height() - height, track.width(), height)
                painter.setBrush(DOCK_BAR_COLOR)
                painter.drawRoundedRect(fill, radius, radius)
                if 6 < height < track.height() - 2:
                    painter.setPen(QPen(DOCK_BAR_EDGE, 1.5))
                    y = fill.top()
                    painter.drawLine(track.left() + 1, y, track.right() - 1, y)
            else:
                width = max(6 if self._percent > 0 else 0, int(round(track.width() * ratio)))
                fill = QRect(track.left(), track.top(), width, track.height())
                painter.setBrush(DOCK_BAR_COLOR)
                painter.drawRoundedRect(fill, radius, radius)
                if 6 < width < track.width() - 2:
                    painter.setPen(QPen(DOCK_BAR_EDGE, 1.5))
                    x = fill.right()
                    painter.drawLine(x, track.top() + 1, x, track.bottom() - 1)
        painter.end()


class _DockGrip(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._vertical = False
        self._scale = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.set_vertical(False)

    def set_vertical(self, vertical: bool) -> None:
        self.apply_metrics(getattr(self, "_scale", 1.0), vertical)

    def apply_metrics(self, scale: float, vertical: Optional[bool] = None) -> None:
        self._scale = scale
        if vertical is not None:
            self._vertical = vertical
        if self._vertical:
            self.setFixedSize(max(12, int(round(16 * scale))), max(6, int(round(8 * scale))))
        else:
            self.setFixedSize(max(8, int(round(10 * scale))), max(16, int(round(22 * scale))))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(200, 206, 214, 180))
        scale = max(1.0, self._scale)
        dot = max(2, int(round(2 * scale)))
        if self._vertical:
            for col in range(3):
                for row in range(2):
                    painter.drawEllipse(
                        int(round(2 * scale + col * 5 * scale)),
                        int(round(1 * scale + row * 4 * scale)),
                        dot,
                        dot,
                    )
        else:
            for col in range(2):
                for row in range(3):
                    painter.drawEllipse(
                        int(round(1 * scale + col * 5 * scale)),
                        int(round(3 * scale + row * 5 * scale)),
                        dot,
                        dot,
                    )
        painter.end()
