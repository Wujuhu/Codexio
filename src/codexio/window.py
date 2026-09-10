from __future__ import annotations

import sys
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QGuiApplication,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from codexio.app_icon import load_app_icon
from codexio.money import usd
from codexio.dock import (
    DOCK_BOTTOM,
    DOCK_LEFT,
    DOCK_NONE,
    DOCK_RIGHT,
    DOCK_TOP,
    DOCKED_EDGES,
    DockStrip,
    SnapPreview,
    choose_dock_edge,
    clamp_dock_size,
    dock_max_size,
    dock_min_size,
    dock_size,
    float_geometry,
    is_horizontal_dock,
    pin_docked_geometry,
    snap_geometry,
    snap_threshold,
)
from codexio.hit_test import (
    CARD_RADIUS,
    HIT_BUTTON,
    HIT_DRAG,
    HIT_RESIZE,
    orb_contains,
    orb_resize_edge,
    resize_edge_on_ring,
)
from codexio.rate_limits import QuotaState, QuotaStatus, format_updated_time, should_show_five_hour
from codexio.settings import (
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    DISPLAY_MODES,
    MAX_WINDOW_HEIGHT,
    MAX_WINDOW_WIDTH,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    QUOTA_SCOPES,
    REFRESH_INTERVALS,
    VISUAL_STYLES,
    AppSettings,
    save_settings,
)
from codexio.visuals import (
    ORB_MAX_SIZE,
    ORB_MIN_SIZE,
    ORB_STYLE,
    STYLE_LABELS,
    PercentAnimator,
    apply_text_shadow,
    create_style_widget,
    display_font,
    display_font_css,
    style_preferred_window_size,
)

INTERVAL_LABELS = {
    30: "30 秒",
    60: "1 分钟",
    300: "5 分钟",
}
MODE_LABELS = {
    "bottom": "底层模式",
    "top": "顶层模式",
    "tray": "托盘模式",
}
SCOPE_LABELS = {
    "auto": "自动（按订阅）",
    "both": "5 hours + 1 week",
    "week": "仅周额度",
}
STATUS_LABELS = {
    QuotaStatus.READING: "正在读取",
    QuotaStatus.OK: "正常",
    QuotaStatus.ERROR: "读取失败",
    QuotaStatus.STALE: "数据过期",
}
STATUS_COLORS = {
    QuotaStatus.READING: "#8AB4F8",
    QuotaStatus.OK: "#7DDEA0",
    QuotaStatus.ERROR: "#FF8A80",
    QuotaStatus.STALE: "#F6D56B",
}
_EDGE_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
    "l": Qt.CursorShape.SizeHorCursor,
    "r": Qt.CursorShape.SizeHorCursor,
    "t": Qt.CursorShape.SizeVerCursor,
    "b": Qt.CursorShape.SizeVerCursor,
}


class QuotaWindow(QWidget):
    def __init__(
        self,
        settings: AppSettings,
        on_refresh: Callable[[], None],
        on_interval_changed: Callable[[int], None],
        on_quit: Callable[[], None],
        on_settings_applied: Optional[Callable[[AppSettings], None]] = None,
        on_open: Optional[Callable[[str, str], None]] = None,
        on_hide: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._on_refresh = on_refresh
        self._on_interval_changed = on_interval_changed
        self._on_quit = on_quit
        self._on_settings_applied = on_settings_applied
        self._on_open = on_open
        self._on_hide = on_hide or self.hide
        self._usage_summary = None
        self._usage_text = ""
        self._state_dirty = True
        self._summary_dirty = False
        self._rendered_week_only = None
        self._ready = False
        self._theme_applied = False
        self._theme_background = (28, 31, 38)
        self._theme_text = None
        self._theme_light = False
        self._state = QuotaState.empty()
        self._suspend_save = False
        self._drag_offset: Optional[QPoint] = None
        self._resize_edge = ""
        self._resize_origin = QPoint()
        self._resize_geom = QRect()
        self._snap_preview = None
        self._preview_edge = DOCK_NONE
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._persist_settings)
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._refresh_status_text)

        self.setObjectName("quotaWindow")
        self.setWindowTitle("Codexio")
        self.setWindowIcon(load_app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._build_ui()
        self._menu = self._build_menu()
        self.apply_visual_style(settings.visual_style, persist=False, reset_size=False)
        self._restore_geometry()
        self.apply_display_mode(settings.display_mode, persist=False)
        self._restore_geometry()
        self._apply_dock(self._settings.dock_edge, persist=False)
        self.apply_theme("dark")
        self._ready = True
        if self.isVisible():
            self._render_pending()
            self._tick.start()

    def context_menu(self) -> QMenu:
        return self._menu

    def apply_usage_summary(self, summary: dict) -> None:
        self._usage_summary = dict(summary)
        self._summary_dirty = True
        tokens = _short_tokens(summary.get("tokens", 0))
        amount = summary.get("usd", 0)
        self._usage_text = "今日 %s Token %s" % (tokens, usd(amount, missing="费用未定价"))
        if self.isVisible():
            self._render_pending()

    def _render_usage_summary(self) -> None:
        summary = self._usage_summary
        if summary is None:
            return
        tokens = _short_tokens(summary.get("tokens", 0))
        amount = summary.get("usd", 0)
        compact_usd = usd(amount, compact=True)
        self._orb_usage.setText("%s\n%s" % (tokens, compact_usd))
        self._refresh_status_text()
        self._apply_minimum_size()
        self._position_orb_usage()

    def apply_theme(self, name: str = "dark") -> None:
        if self._theme_applied:
            return
        from codexio.theme import theme_colors, apply_dark_menu
        colors = theme_colors("dark")
        # Preserve the user's transparency while adapting foreground contrast.
        background = QColor(colors.get("surface", colors.get("card", "#181b24")))
        self._theme_background = (background.red(), background.green(), background.blue())
        text = colors.get("text", "#eff2f8")
        self._theme_text = text
        self._theme_light = False
        muted = colors.get("muted", "#9ca7bb")
        css = _STYLESHEET % display_font_css()
        css += "\n#quotaWindow QLabel { color: %s; } #rowReset, #mutedLabel { color: %s; }" % (text, muted)
        self.setStyleSheet(css)
        apply_dark_menu(self._menu)
        for label in self.findChildren(QLabel):
            label.setGraphicsEffect(None)
        self._orb_usage.setStyleSheet("color: #f2fff9; background: transparent;")
        self._theme_applied = True
        self._refresh_status_text()
        self.update()

    def current_settings(self) -> AppSettings:
        return self._settings

    def apply_settings(self, settings: AppSettings) -> None:
        self.apply_visual_style(settings.visual_style, persist=False)
        self.apply_display_mode(settings.display_mode, persist=False)
        self.apply_quota_scope(settings.quota_scope, persist=False)
        self._settings.refresh_interval_seconds = settings.refresh_interval_seconds
        self._settings.codex_path = settings.codex_path
        self._apply_full_settings(settings)

    def persist_settings(self) -> None:
        if self._suspend_save:
            return
        self._persist_settings()

    def apply_state(self, state: QuotaState) -> None:
        self._state = state
        self._state_dirty = True
        if self.isVisible():
            self._render_pending()

    def _render_pending(self) -> None:
        if not self.isVisible():
            return
        if self._state_dirty:
            if self._is_docked():
                self._ensure_dock_strip().apply(self._state)
            else:
                self._current_style().apply(self._state)
            self._sync_quota_layout(resize_dock=self._rendered_week_only is not None
                                    and self._rendered_week_only != self._week_only())
            self._rendered_week_only = self._week_only()
            self._state_dirty = False
        if self._summary_dirty:
            self._summary_dirty = False
            self._render_usage_summary()
        self._refresh_status_text()
        self._sync_hover_tip()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._ready:
            self._render_pending()
            self._tick.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._tick.stop()
        self._hide_snap_preview()
        for animator in self.findChildren(PercentAnimator):
            animator.finish()
        super().hideEvent(event)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        width, height = self._target_size()
        return QSize(width, height)

    def apply_display_mode(self, mode: str, persist: bool = True) -> None:
        if mode not in DISPLAY_MODES:
            mode = "top"
        self._settings.display_mode = mode
        if self.isVisible() and self.width() >= MIN_WINDOW_WIDTH and self.height() >= MIN_WINDOW_HEIGHT:
            geom = self.geometry()
        else:
            geom = self._target_geometry()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if mode == "top":
            flags |= Qt.WindowType.WindowStaysOnTopHint
        elif mode == "bottom":
            flags |= Qt.WindowType.WindowStaysOnBottomHint
        self.setWindowFlags(flags)
        self.setGeometry(geom)
        if mode == "tray":
            self._hide_snap_preview()
            self.hide()
        else:
            self.show()
            self.setGeometry(geom)
            self.resize(geom.size())
            if mode == "bottom":
                self.lower()
                _send_to_bottom(self)
        self._sync_mode_actions()
        if persist:
            self._schedule_save()

    def apply_visual_style(self, style_name: str, persist: bool = True, reset_size: bool = False) -> None:
        if style_name not in VISUAL_STYLES:
            style_name = "classic"
        self._settings.visual_style = style_name
        if not self._is_docked():
            widget = self._current_style()
            self._stack.setCurrentWidget(widget)
            if self.isVisible():
                widget.apply(self._state)
            else:
                self._state_dirty = True
        self._sync_quota_layout()
        self._sync_float_chrome()
        self._apply_minimum_size()
        if not self._is_docked():
            width, height = self._size_for_style_switch(style_name, reset_size)
            if width is not None and height is not None:
                self._settings.window_width = width
                self._settings.window_height = height
                self.resize(width, height)
        self._sync_style_actions()
        self._sync_hover_tip()
        if persist:
            self._capture_geometry()
            self._persist_settings()

    def apply_quota_scope(self, scope: str, persist: bool = True) -> None:
        if scope not in QUOTA_SCOPES:
            scope = "auto"
        self._settings.quota_scope = scope
        self._sync_quota_layout(resize_dock=True)
        self._sync_scope_actions()
        self._sync_hover_tip()
        if persist:
            self._schedule_save()

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self._hide_snap_preview()
            self.hide()
            return
        if self._settings.display_mode == "tray":
            flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
            geom = self.geometry()
            self.setWindowFlags(flags)
            self.setGeometry(geom)
            self.show()
            self.raise_()
            return
        self.show()
        self.raise_()

    def shows_five_hour(self) -> bool:
        return self._shows_five_hour()

    def _build_ui(self) -> None:
        title = QLabel("Codexio")
        title.setObjectName("title")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_text_shadow(title)
        self._refresh_button = QToolButton()
        self._refresh_button.setObjectName("refreshButton")
        self._refresh_button.setText("刷新")
        self._refresh_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._refresh_button.clicked.connect(self._on_refresh)

        self._header = _ChromeBar(self)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(6, 2, 4, 2)
        header_layout.setSpacing(4)
        header_layout.addWidget(_DragGrip(self._header))
        brand_icon = QLabel(self._header)
        brand_icon.setPixmap(load_app_icon().pixmap(18, 18))
        brand_icon.setFixedSize(18, 18)
        brand_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(brand_icon)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(self._refresh_button)

        self._stack = QStackedWidget()
        self._stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._stack.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._styles = {}

        self._status = QLabel("正在读取")
        self._status.setObjectName("status")
        self._status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        apply_text_shadow(self._status)

        self._float_page = _DragSurface(self)
        self._orb_usage = QLabel(self._float_page)
        self._orb_usage.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._orb_usage.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._orb_usage.setFont(display_font(10))
        self._orb_usage.hide()
        float_layout = QVBoxLayout(self._float_page)
        float_layout.setContentsMargins(0, 0, 0, 0)
        float_layout.setSpacing(8)
        float_layout.addWidget(self._header)
        float_layout.addWidget(self._stack, 1)
        float_layout.addWidget(self._status)

        self._dock_strip = None
        self._pages = _DragStacked(self)
        self._pages.addWidget(self._float_page)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(14, 12, 14, 12)
        self._root_layout.setSpacing(8)
        self._root_layout.addWidget(self._pages)
        self.setStyleSheet(_STYLESHEET % display_font_css())
        self.setFont(display_font(13))

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        # Configuration lives in the dashboard; retain action dictionaries for
        # the existing window-style synchronisation helpers.
        self._mode_actions = {}
        self._style_actions = {}
        self._scope_actions = {}
        self._interval_actions = {}
        open_action = menu.addAction("打开主界面")
        open_action.triggered.connect(lambda: self._on_open and self._on_open("overview", "today"))
        details = menu.addMenu("Token 使用详情")
        for period, label in (("today", "今日"), ("week", "近 7 天"), ("month", "近 30 天"), ("all", "全部历史")):
            action = details.addAction(label)
            action.triggered.connect(lambda checked=False, value=period: self._on_open and self._on_open("usage", value))
        menu.addAction("立即刷新").triggered.connect(self._on_refresh)
        menu.addSeparator()
        self._undock_action = menu.addAction("取消停靠")
        self._undock_action.triggered.connect(lambda: self._apply_dock(DOCK_NONE))
        self._undock_action.setVisible(False)
        menu.addAction("关闭悬浮窗").triggered.connect(self._on_hide)
        return menu

    def _current_style(self):
        name = self._settings.visual_style
        if name not in self._styles:
            self._release_styles()
            widget = create_style_widget(name, self._stack)
            self._styles[name] = widget
            self._stack.addWidget(widget)
            widget.set_show_five(self._shows_five_hour())
            self._prepare_dynamic_widget(widget)
            self._state_dirty = True
        return self._styles[name]

    def _prepare_dynamic_widget(self, widget) -> None:
        if self._theme_applied:
            for label in widget.findChildren(QLabel):
                label.setGraphicsEffect(None)

    def _release_styles(self) -> None:
        for widget in self._styles.values():
            widget.hide()
            for animator in widget.findChildren(PercentAnimator):
                animator.finish()
            self._stack.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._styles.clear()

    def _ensure_dock_strip(self):
        if self._dock_strip is None:
            self._dock_strip = DockStrip(self, self._on_refresh, self)
            self._pages.addWidget(self._dock_strip)
            self._prepare_dynamic_widget(self._dock_strip)
            self._state_dirty = True
        return self._dock_strip

    def _release_dock_strip(self) -> None:
        strip = self._dock_strip
        if strip is not None:
            self._dock_strip = None
            strip.hide()
            for animator in strip.findChildren(PercentAnimator):
                animator.finish()
            self._pages.removeWidget(strip)
            strip.setParent(None)
            strip.deleteLater()

    def _change_interval(self, seconds: int) -> None:
        self._settings.refresh_interval_seconds = seconds
        self._sync_interval_actions()
        self._on_interval_changed(seconds)
        self._schedule_save()

    def _reset_size(self) -> None:
        self.apply_visual_style(self._settings.visual_style, persist=True, reset_size=True)

    def _apply_full_settings(self, settings: AppSettings, persist: bool = True) -> None:
        settings = settings.normalized()
        self._apply_appearance(settings, persist=False)
        if persist:
            self._persist_settings()
        if self._on_settings_applied is not None:
            self._on_settings_applied(self._settings)

    def _apply_appearance(self, settings: AppSettings, persist: bool = True) -> None:
        self._settings.background_transparent = settings.background_transparent
        self._settings.background_opacity = settings.background_opacity
        self._settings.show_border = settings.show_border
        self._settings.border_color = settings.border_color
        self.update()
        if persist:
            self._persist_settings()

    def _sync_mode_actions(self) -> None:
        action = self._mode_actions.get(self._settings.display_mode)
        if action is not None:
            action.setChecked(True)

    def _sync_style_actions(self) -> None:
        action = self._style_actions.get(self._settings.visual_style)
        if action is not None:
            action.setChecked(True)

    def _sync_interval_actions(self) -> None:
        action = self._interval_actions.get(self._settings.refresh_interval_seconds)
        if action is not None:
            action.setChecked(True)

    def _sync_scope_actions(self) -> None:
        action = self._scope_actions.get(self._settings.quota_scope)
        if action is not None:
            action.setChecked(True)

    def _shows_five_hour(self) -> bool:
        return should_show_five_hour(self._settings.quota_scope, self._state)

    def _week_only(self) -> bool:
        return not self._shows_five_hour()

    def _sync_quota_layout(self, resize_dock: bool = False) -> None:
        week_only = self._week_only()
        if not self.isVisible() and self._ready:
            self._state_dirty = True
        elif not self._is_docked():
            self._current_style().set_show_five(not week_only)
        if self._dock_strip is not None:
            self._dock_strip.set_week_only(week_only)
        if resize_dock and self._is_docked():
            self._apply_dock(self._settings.dock_edge, persist=False)

    def _refresh_status_text(self) -> None:
        if not self.isVisible():
            return
        label = STATUS_LABELS.get(self._state.status, self._state.message)
        updated = format_updated_time(self._state.last_success_at)
        color = STATUS_COLORS.get(self._state.status, "#D0D5DD")
        detail = self._state.message if self._state.status == QuotaStatus.ERROR else updated
        self._status.setText(self._usage_text if self._usage_summary is not None else "%s · %s" % (label, detail))
        style = "color: %s; font-size: %dpx;" % (self._theme_text or color, 15 if self._usage_summary is not None else 12)
        if self._status.styleSheet() != style:
            self._status.setStyleSheet(style)
        if self._dock_strip is not None:
            self._dock_strip.setToolTip("%s · %s" % (label, detail))
        self._status.setToolTip("%s · %s" % (label, detail))
        if self._usage_summary and self._usage_summary.get("unpriced_tokens", 0):
            self._status.setToolTip(self._status.toolTip() + "\n金额仅含已定价部分，%s Token 尚未定价" % self._usage_summary["unpriced_tokens"])
        if self._theme_text is not None:
            self._tint_percent_labels()
        self._sync_hover_tip()

    def _tint_percent_labels(self) -> None:
        for label in self.findChildren(QLabel):
            if label.objectName() not in ("rowPercent", "heroPercent", "dockPercent"):
                continue
            try:
                percent = float(label.text().rstrip("%"))
            except ValueError:
                style = "color: %s;" % self._theme_text
                if label.styleSheet() != style:
                    label.setStyleSheet(style)
                continue
            if self._theme_light:
                color = "#C33E4D" if percent <= 20 else "#9B6913" if percent <= 50 else "#197950"
            else:
                color = "#FF8A80" if percent <= 20 else "#F6D56B" if percent <= 50 else "#7DDEA0"
            style = "color: %s;" % color
            if label.styleSheet() != style:
                label.setStyleSheet(style)

    def _is_docked(self) -> bool:
        return self._settings.dock_edge in DOCKED_EDGES

    def _is_orb_style(self) -> bool:
        return self._settings.visual_style == ORB_STYLE

    def _is_orb_float(self) -> bool:
        return self._is_orb_style() and not self._is_docked()

    def _sync_hover_tip(self) -> None:
        if not self.isVisible():
            return
        if self._is_orb_float():
            tip = self._current_style().hover_text()
            if self._usage_summary is not None:
                tip += "\n" + self._usage_text
            self.setToolTip(tip)
            return
        self.setToolTip("")

    def _sync_float_chrome(self) -> None:
        orb = self._is_orb_float()
        self._header.setVisible(not orb)
        self._status.setVisible(not orb)
        self._position_orb_usage()
        if self._is_docked():
            return
        if orb:
            self._root_layout.setContentsMargins(0, 0, 0, 0)
            self._root_layout.setSpacing(0)
            return
        self._root_layout.setContentsMargins(14, 12, 14, 12)
        self._root_layout.setSpacing(8)

    def _size_for_style_switch(self, style_name: str, reset_size: bool) -> Tuple[Optional[int], Optional[int]]:
        preferred = style_preferred_window_size(style_name)
        current = (self._settings.window_width, self._settings.window_height)
        if style_name == ORB_STYLE:
            if reset_size or not _is_orb_size(current[0], current[1]):
                return preferred
            return current
        if reset_size or _is_orb_size(current[0], current[1]):
            return preferred
        return (None, None)

    def _docked_min_size(self, edge: str) -> Tuple[int, int]:
        return dock_min_size(edge, self._week_only())

    def _apply_minimum_size(self) -> None:
        edge = self._settings.dock_edge
        if edge in DOCKED_EDGES:
            width, height = self._docked_min_size(edge)
            self.setMinimumSize(width, height)
            return
        if self._is_orb_style():
            minimum = max(96, ORB_MIN_SIZE) if self._usage_summary is not None else ORB_MIN_SIZE
            self.setMinimumSize(minimum, minimum)
            return
        widget = self._current_style()
        minimum = widget.minimum_size() + QSize(28, 76)
        if self._usage_summary is not None:
            minimum.setWidth(max(minimum.width(), self._status.fontMetrics().horizontalAdvance(self._usage_text) + 28))
        self.setMinimumSize(minimum)

    def _sync_dock_layout(self) -> None:
        edge = self._settings.dock_edge
        if edge in DOCKED_EDGES:
            self._release_styles()
            self._ensure_dock_strip()
            self._dock_strip.set_edge(edge)
            self._dock_strip.set_week_only(self._week_only())
            if self.isVisible():
                self._dock_strip.apply(self._state)
            else:
                self._state_dirty = True
            self._pages.setCurrentWidget(self._dock_strip)
            if is_horizontal_dock(edge):
                self._root_layout.setContentsMargins(8, 6, 8, 6)
            else:
                self._root_layout.setContentsMargins(4, 8, 4, 8)
            self._root_layout.setSpacing(0)
        else:
            self._release_dock_strip()
            widget = self._current_style()
            self._stack.setCurrentWidget(widget)
            if self.isVisible():
                widget.apply(self._state)
            self._pages.setCurrentWidget(self._float_page)
            self._sync_float_chrome()
        self._apply_minimum_size()
        if hasattr(self, "_undock_action"):
            self._undock_action.setVisible(self._is_docked())

    def _apply_dock(self, edge: str, persist: bool = True) -> None:
        if edge not in (DOCK_NONE, *DOCKED_EDGES):
            edge = DOCK_NONE
        current = self.geometry()
        float_size = self._float_size()
        screen = self._screen_rect()
        if edge == DOCK_NONE:
            geom = float_geometry(current, screen, float_size)
        else:
            geom = snap_geometry(
                edge, current, screen, self._week_only(), self._size_for_snap(edge)
            )
        self._settings.dock_edge = edge
        self._suspend_save = True
        self._sync_dock_layout()
        self.setGeometry(geom)
        self.resize(geom.size())
        self._suspend_save = False
        if persist:
            self._schedule_save()

    def _snap_after_drag(self) -> None:
        edge = choose_dock_edge(self.geometry(), self._screen_rect(), snap_threshold(self._is_docked()))
        if edge != self._settings.dock_edge:
            self._apply_dock(edge, persist=False)
            return
        if edge != DOCK_NONE:
            geom = snap_geometry(
                edge,
                self.geometry(),
                self._screen_rect(),
                self._week_only(),
                (self.width(), self.height()),
            )
            self.setGeometry(geom)

    def _update_snap_preview(self) -> None:
        edge = choose_dock_edge(self.geometry(), self._screen_rect(), snap_threshold(self._is_docked()))
        self._preview_edge = edge
        if edge == DOCK_NONE:
            self._hide_snap_preview()
            return
        geom = snap_geometry(
            edge,
            self.geometry(),
            self._screen_rect(),
            self._week_only(),
            self._size_for_snap(edge),
        )
        if self._snap_preview is None:
            self._snap_preview = SnapPreview()
            self.destroyed.connect(self._snap_preview.deleteLater)
        self._snap_preview.show_at(geom, edge)
        self.raise_()

    def _hide_snap_preview(self) -> None:
        self._preview_edge = DOCK_NONE
        if self._snap_preview is not None:
            self._snap_preview.hide_preview()

    def _screen_rect(self) -> QRect:
        handle = self.windowHandle()
        screen = handle.screen() if handle is not None else None
        if screen is None:
            screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return QRect(0, 0, 1280, 720)
        return screen.availableGeometry()

    def _float_size(self) -> Tuple[int, int]:
        default_w, default_h = style_preferred_window_size(self._settings.visual_style)
        if self._is_orb_style():
            width = self._settings.window_width or default_w
            height = self._settings.window_height or default_h
            side = max(width, height)
            side = max(ORB_MIN_SIZE, min(ORB_MAX_SIZE, side))
            return (side, side)
        width = self._settings.window_width or default_w or DEFAULT_WINDOW_WIDTH
        height = self._settings.window_height or default_h or DEFAULT_WINDOW_HEIGHT
        return (
            max(MIN_WINDOW_WIDTH, min(MAX_WINDOW_WIDTH, width)),
            max(MIN_WINDOW_HEIGHT, min(MAX_WINDOW_HEIGHT, height)),
        )

    def _saved_dock_size(self, edge: str) -> Tuple[int, int]:
        week_only = self._week_only()
        default = dock_size(edge, week_only)
        if is_horizontal_dock(edge):
            width = self._settings.dock_top_width
            height = self._settings.dock_top_height
        elif edge in (DOCK_LEFT, DOCK_RIGHT):
            width = self._settings.dock_side_width
            height = self._settings.dock_side_height
        else:
            return default
        if width is None:
            width = default[0]
        if height is None:
            height = default[1]
        width, height = clamp_dock_size(edge, width, height, week_only)
        min_w, min_h = self._docked_min_size(edge)
        return (max(min_w, width), max(min_h, height))

    def _size_for_snap(self, edge: str) -> Tuple[int, int]:
        if edge == self._settings.dock_edge and self._is_docked():
            width, height = clamp_dock_size(edge, self.width(), self.height(), self._week_only())
            min_w, min_h = self._docked_min_size(edge)
            return (max(min_w, width), max(min_h, height))
        return self._saved_dock_size(edge)

    def _target_size(self) -> Tuple[int, int]:
        if self._is_docked():
            return self._saved_dock_size(self._settings.dock_edge)
        return self._float_size()

    def _target_geometry(self) -> QRect:
        x, y = self._settings.window_x, self._settings.window_y
        screen = QGuiApplication.primaryScreen()
        screen_rect = screen.availableGeometry() if screen is not None else QRect(80, 80, 1280, 720)
        if self._is_docked():
            seed = QRect(x or screen_rect.left(), y or screen_rect.top(), 10, 10)
            return snap_geometry(
                self._settings.dock_edge,
                seed,
                screen_rect,
                self._week_only(),
                self._saved_dock_size(self._settings.dock_edge),
            )
        width, height = self._float_size()
        if x is None or y is None or not _is_on_screen(x, y):
            x = screen_rect.right() - width - 18
            y = screen_rect.bottom() - height - 56
        return QRect(x, y, width, height)

    def _restore_geometry(self) -> None:
        geom = self._target_geometry()
        self.setGeometry(geom)
        self.resize(geom.size())

    def _capture_geometry(self) -> None:
        if self._suspend_save:
            return
        geom = self.geometry()
        self._settings.window_x = geom.x()
        self._settings.window_y = geom.y()
        if self._is_docked():
            if is_horizontal_dock(self._settings.dock_edge):
                self._settings.dock_top_width = geom.width()
                self._settings.dock_top_height = geom.height()
            else:
                self._settings.dock_side_width = geom.width()
                self._settings.dock_side_height = geom.height()
            return
        self._settings.window_width = geom.width()
        self._settings.window_height = geom.height()

    def _schedule_save(self) -> None:
        self._capture_geometry()
        if self._suspend_save:
            return
        self._save_timer.start(400)

    def _persist_settings(self) -> None:
        self._capture_geometry()
        save_settings(self._settings)

    def _pinned_resize_edges(self) -> str:
        if self._settings.dock_edge == DOCK_TOP:
            return "t"
        if self._settings.dock_edge == DOCK_BOTTOM:
            return "b"
        if self._settings.dock_edge == DOCK_LEFT:
            return "l"
        if self._settings.dock_edge == DOCK_RIGHT:
            return "r"
        return ""

    def _resize_edge_at(self, pos: QPoint) -> str:
        if self._is_orb_float():
            return orb_resize_edge(pos, self.rect(), blocked=self._pinned_resize_edges())
        return resize_edge_on_ring(pos, self.rect(), blocked=self._pinned_resize_edges())

    def hit_kind_at(self, pos: QPoint) -> Optional[str]:
        if self._is_orb_float():
            if not orb_contains(pos, self.rect()):
                return None
            if self._resize_edge_at(pos):
                return HIT_RESIZE
            return HIT_DRAG
        if not self.rect().contains(pos):
            return None
        if self._button_hit_rect().contains(pos):
            return HIT_BUTTON
        if self._resize_edge_at(pos):
            return HIT_RESIZE
        return HIT_DRAG

    def _visible_refresh_button(self):
        if self._is_docked():
            return self._ensure_dock_strip().refresh_button
        return self._refresh_button

    def _button_hit_rect(self) -> QRect:
        button = self._visible_refresh_button()
        origin = button.mapTo(self, QPoint(0, 0))
        return QRect(origin, button.size())

    def _event_window_pos(self, event) -> QPoint:
        return self.mapFromGlobal(event.globalPosition().toPoint())

    def _update_cursor(self, pos: QPoint) -> None:
        if self._drag_offset is not None:
            cursor = Qt.CursorShape.ClosedHandCursor
        else:
            kind = self.hit_kind_at(pos)
            if kind == HIT_BUTTON:
                return
            if kind == HIT_RESIZE:
                edge = self._resize_edge_at(pos)
                cursor = _EDGE_CURSORS.get(edge, Qt.CursorShape.SizeAllCursor)
            elif kind == HIT_DRAG:
                cursor = Qt.CursorShape.OpenHandCursor
            else:
                cursor = Qt.CursorShape.ArrowCursor
        self.setCursor(cursor)
        self._header.setCursor(cursor)
        if self._dock_strip is not None:
            self._dock_strip.setCursor(cursor)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        self._menu.exec(global_pos)

    def _handle_mouse_press(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        local = self._event_window_pos(event)
        kind = self.hit_kind_at(local)
        if kind == HIT_BUTTON:
            return
        if kind == HIT_RESIZE:
            self._resize_edge = self._resize_edge_at(local)
            self._resize_origin = event.globalPosition().toPoint()
            self._resize_geom = self.geometry()
            return
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        self._update_cursor(local)

    def _handle_mouse_move(self, event) -> None:
        global_pos = event.globalPosition().toPoint()
        local = self._event_window_pos(event)
        if self._resize_edge and event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_resize(global_pos)
        elif self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(global_pos - self._drag_offset)
            self._update_snap_preview()
        else:
            self._update_cursor(local)

    def _handle_mouse_release(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was_drag = self._drag_offset is not None
        was_resize = bool(self._resize_edge)
        if not was_drag and not was_resize:
            return
        self._drag_offset = None
        self._resize_edge = ""
        self._hide_snap_preview()
        self._update_cursor(self._event_window_pos(event))
        if was_drag:
            self._snap_after_drag()
        self._schedule_save()

    def _handle_mouse_double_click(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.hit_kind_at(self._event_window_pos(event)) == HIT_DRAG:
            self._on_refresh()

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        self._show_context_menu(event.globalPos())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._handle_mouse_press(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        self._handle_mouse_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._handle_mouse_release(event)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        self._handle_mouse_double_click(event)
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if not self._is_orb_float():
            super().wheelEvent(event)
            return
        local = self._event_window_pos(event)
        if not orb_contains(local, self.rect()):
            super().wheelEvent(event)
            return
        steps = event.angleDelta().y()
        if steps == 0:
            return
        self._nudge_orb_size(12 if steps > 0 else -12)
        event.accept()

    def _nudge_orb_size(self, delta: int) -> None:
        side = max(ORB_MIN_SIZE, min(ORB_MAX_SIZE, self.width() + int(delta)))
        if side == self.width() and side == self.height():
            return
        geom = QRect(self.geometry())
        center = geom.center()
        geom.setSize(QSize(side, side))
        geom.moveCenter(center)
        self.setGeometry(geom)
        self._settings.window_width = side
        self._settings.window_height = side
        self._schedule_save()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Layered/translucent windows ignore clicks on fully transparent pixels.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 1))
        if self._is_orb_float():
            painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
            painter.end()
            return
        rect = self.rect().adjusted(1, 1, -2, -2)
        painter.drawRect(self.rect())
        alpha = self._settings.background_alpha()
        if alpha > 0:
            painter.setBrush(QColor(*self._theme_background, alpha))
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._settings.show_border:
            painter.setPen(QPen(QColor(self._settings.border_color), 1))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        if alpha > 0 or self._settings.show_border:
            radius = 10 if self._is_docked() else CARD_RADIUS
            painter.drawRoundedRect(rect, radius, radius)
        painter.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._is_docked() and self._dock_strip is not None:
            self._dock_strip._apply_metrics()
        self._position_orb_usage()
        self._schedule_save()

    def _position_orb_usage(self) -> None:
        if not hasattr(self, "_orb_usage"):
            return
        visible = self._is_orb_float() and self._usage_summary is not None
        self._orb_usage.setVisible(visible)
        if visible:
            width, height = self._float_page.width(), self._float_page.height()
            self._orb_usage.setFont(display_font(max(10, min(14, width // 11))))
            height_needed = max(self._orb_usage.fontMetrics().lineSpacing() * 2, int(height * .25))
            self._orb_usage.setGeometry(int(width * .12), int(height * .65), int(width * .76), height_needed)
            self._orb_usage.raise_()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._hide_snap_preview()
        if self._snap_preview is not None:
            self._snap_preview.close()
        super().closeEvent(event)

    def _resize_limits(self) -> Tuple[int, int, int, int]:
        edge = self._settings.dock_edge
        if edge in DOCKED_EDGES:
            min_w, min_h = self._docked_min_size(edge)
            max_w, max_h = dock_max_size(edge)
            return min_w, min_h, max_w, max_h
        if self._is_orb_style():
            minimum = max(96, ORB_MIN_SIZE) if self._usage_summary is not None else ORB_MIN_SIZE
            return minimum, minimum, ORB_MAX_SIZE, ORB_MAX_SIZE
        return MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT, MAX_WINDOW_WIDTH, MAX_WINDOW_HEIGHT

    def _apply_resize(self, global_pos: QPoint) -> None:
        delta = global_pos - self._resize_origin
        geom = QRect(self._resize_geom)
        edge = self._resize_edge
        if "l" in edge:
            geom.setLeft(self._resize_geom.left() + delta.x())
        if "r" in edge:
            geom.setRight(self._resize_geom.right() + delta.x())
        if "t" in edge:
            geom.setTop(self._resize_geom.top() + delta.y())
        if "b" in edge:
            geom.setBottom(self._resize_geom.bottom() + delta.y())
        min_w, min_h, max_w, max_h = self._resize_limits()
        if geom.width() < min_w:
            if "l" in edge:
                geom.setLeft(geom.right() - min_w)
            else:
                geom.setWidth(min_w)
        if geom.height() < min_h:
            if "t" in edge:
                geom.setTop(geom.bottom() - min_h)
            else:
                geom.setHeight(min_h)
        geom.setWidth(min(max_w, max(min_w, geom.width())))
        geom.setHeight(min(max_h, max(min_h, geom.height())))
        if self._is_orb_float():
            side = max(geom.width(), geom.height())
            if edge in ("l", "r"):
                side = geom.width()
            elif edge in ("t", "b"):
                side = geom.height()
            side = max(ORB_MIN_SIZE, min(ORB_MAX_SIZE, side))
            pinned_right = geom.right()
            pinned_bottom = geom.bottom()
            geom.setSize(QSize(side, side))
            if "l" in edge:
                geom.moveRight(pinned_right)
            if "t" in edge:
                geom.moveBottom(pinned_bottom)
        if self._is_docked():
            geom = pin_docked_geometry(self._settings.dock_edge, geom, self._screen_rect())
        self.setGeometry(geom)


class _MouseToHostMixin:
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


class _DragSurface(_MouseToHostMixin, QWidget):
    def __init__(self, host: QuotaWindow) -> None:
        super().__init__()
        self._host = host
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class _DragStacked(_MouseToHostMixin, QStackedWidget):
    def __init__(self, host: QuotaWindow) -> None:
        super().__init__()
        self._host = host
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class _ChromeBar(_DragSurface):
    def __init__(self, host: QuotaWindow) -> None:
        super().__init__(host)
        self.setObjectName("chromeHeader")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("任意位置拖动可移动窗口，边缘拖动可调节大小，右键打开菜单")


class _DragGrip(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(200, 206, 214, 180))
        for col in range(2):
            for row in range(3):
                painter.drawEllipse(1 + col * 5, 2 + row * 5, 2, 2)
        painter.end()


def _short_tokens(value: int) -> str:
    number = int(value)
    if number >= 1_000_000_000:
        return "%.2fB" % (number / 1_000_000_000)
    if number >= 1_000_000:
        return "%.2fM" % (number / 1_000_000)
    if number >= 1000:
        return "%.1fk" % (number / 1000)
    return str(number)


def _is_orb_size(width: Optional[int], height: Optional[int]) -> bool:
    if width is None or height is None:
        return False
    return abs(width - height) <= 12 and ORB_MIN_SIZE <= width <= ORB_MAX_SIZE and ORB_MIN_SIZE <= height <= ORB_MAX_SIZE


def _is_on_screen(x: int, y: int) -> bool:
    for screen in QGuiApplication.screens():
        if screen.availableGeometry().contains(x + 16, y + 16):
            return True
    return False


def _send_to_bottom(widget: QWidget) -> None:
    if sys.platform != "win32":
        widget.lower()
        return
    import ctypes

    hwnd_bottom = 1
    swp_nomove = 0x0002
    swp_nosize = 0x0001
    swp_noactivate = 0x0010
    ctypes.windll.user32.SetWindowPos(
        int(widget.winId()),
        hwnd_bottom,
        0,
        0,
        0,
        0,
        swp_nomove | swp_nosize | swp_noactivate,
    )


_STYLESHEET = """
#quotaWindow, #quotaWindow QLabel, #quotaWindow QToolButton, QStackedWidget {
    font-family: %s;
    font-weight: 400;
}
#quotaWindow, QStackedWidget {
    background: transparent;
    border: none;
}
#chromeHeader {
    background: rgba(255, 255, 255, 18);
    border-radius: 8px;
}
#title {
    color: #F5F7FA;
    font-size: 17px;
    font-weight: 400;
    background: transparent;
}
#refreshButton {
    color: #AECBFA;
    background: transparent;
    border: none;
    font-size: 13px;
    padding: 2px 4px;
}
#refreshButton:hover {
    color: #D2E3FC;
}
#rowTitle, #rowReset, #status, #mutedLabel {
    color: #E8EAED;
    font-size: 14px;
    background: transparent;
}
#rowPercent, #heroPercent {
    font-size: 22px;
    font-weight: 400;
    background: transparent;
}
#heroPercent {
    font-size: 32px;
}
#dockStrip {
    background: transparent;
}
#dockTitle {
    color: #E8EAED;
    background: transparent;
}
#dockPercent {
    font-weight: 400;
    background: transparent;
}
"""
