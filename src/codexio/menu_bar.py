"""macOS menu bar preview. It only renders worker snapshots; never scans logs."""
from __future__ import annotations

import copy
import math
import time
from datetime import datetime

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QSizePolicy, QSystemTrayIcon, QVBoxLayout,
)

from codexio.app_icon import render_app_pixmap
from codexio.charts import compact_number, parse_timestamp
from codexio.dashboard import LineLimitedText
from codexio.money import usd
from codexio.rate_limits import QuotaState, QuotaStatus, format_reset_time, stale_after_seconds
from codexio.settings import AppSettings
from codexio.theme import apply_theme, theme_colors
from codexio.user_requests import REQUEST_STATUSES
from codexio.usage_collector import user_message_preview


def label(text="", *, name="", muted=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    widget.setProperty("muted", muted)
    return widget


def menu_bar_icon() -> QIcon:
    """A template version of the existing Quantum X mark, crisp on Retina."""
    pixmap = QPixmap(36, 36)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("black"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    for direction in (-1, 1):
        path = QPainterPath()
        path.moveTo(9 + direction * 3, 3)
        path.lineTo(9 + direction * 7, 9)
        path.lineTo(9 + direction * 3, 15)
        painter.drawPath(path)
    painter.drawLine(QPoint(7, 7), QPoint(11, 11))
    painter.drawLine(QPoint(7, 11), QPoint(11, 7))
    painter.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)
    return icon


def popup_position(anchor: QRect, size: QSize, available: QRect) -> QPoint:
    """Qt screen coordinates include negative origins on secondary displays."""
    left, top = available.left() + 8, available.top() + 6
    right = max(left, available.right() - size.width() - 7)
    bottom = max(top, available.bottom() - size.height() - 7)
    x = max(left, min(anchor.center().x() - size.width() // 2, right))
    y = max(top, min(anchor.bottom() + 8, bottom))
    return QPoint(x, y)


def cost_text(value, status="priced") -> str:
    if status == "unmetered":
        return "待计量"
    if status in ("unpriced", "invalid"):
        return "未定价"
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return "—"
    return usd(value)


class QuotaPreview(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setProperty("previewCard", True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 9, 8, 9)
        layout.setSpacing(5)
        row = QHBoxLayout()
        row.addWidget(label(title))
        self.value = label("—", name="quotaValue")
        row.addWidget(self.value, 1, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(row)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        layout.addWidget(self.bar)
        self.reset = label("重置时间待获取", muted=True)
        self.reset.setWordWrap(True)
        layout.addWidget(self.reset)

    def render(self, view, now, theme):
        colors = theme_colors(theme)
        value = view.remaining_percent
        self.value.setText("额度待获取" if value is None else f"{value}% 剩余")
        self.bar.setValue(value or 0)
        color = colors["quota_high"] if value is not None and value >= 50 else colors["quota_mid"] if value is not None and value >= 20 else colors["quota_low"]
        reset = view.resets_at
        if reset is None:
            text = "重置时间待获取"
        elif reset <= now:
            text = "已到重置时间 · 等待额度更新"
            color = colors["muted"]
        else:
            seconds = math.ceil((reset - now).total_seconds())
            days, hours, minutes = seconds // 86400, seconds % 86400 // 3600, seconds % 3600 // 60
            countdown = f"{days} 天 {hours} 小时" if days else f"{hours} 小时 {minutes} 分" if hours else f"{max(1, minutes)} 分钟"
            text = f"{format_reset_time(reset, now)} 重置 · {countdown}后"
        self.reset.setText(text)
        self.bar.setStyleSheet("QProgressBar { background: %s; border: none; border-radius: 4px; } QProgressBar::chunk { background: %s; border-radius: 4px; }" % (colors["raised"], color))
        self.setAccessibleDescription(self.value.text() + "；" + text)


class MenuBarPreview(QFrame):
    def __init__(self, settings, config, *, on_open, on_quit):
        super().__init__(None, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("menuBarPreview")
        self.setWindowTitle("Codexio 用量预览")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._settings = settings.normalized()
        self._theme = config.get("theme", "system")
        self._quota = QuotaState.empty()
        self._data = None
        self._loading = {}
        self.last_hidden_at = 0.0
        self._on_open = on_open
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        surface = QFrame()
        surface.setObjectName("previewSurface")
        layout.addWidget(surface)
        self.body = QVBoxLayout(surface)
        self.body.setContentsMargins(10, 12, 10, 12)
        self.body.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(5)
        icon = label()
        brand_pixmap = render_app_pixmap(40)
        brand_pixmap.setDevicePixelRatio(2)
        icon.setPixmap(brand_pixmap)
        header.addWidget(icon)
        header.addWidget(label("Codexio", name="previewBrand"), 1)
        self.open_button = QPushButton("主界面")
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(lambda: self._open("overview"))
        header.addWidget(self.open_button)
        self.body.addLayout(header)
        self.quota_status = label("正在连接 Codex", muted=True)
        self.quota_status.setWordWrap(True)
        self.body.addWidget(self.quota_status)
        self.five_hour = QuotaPreview("5 小时额度")
        self.week = QuotaPreview("每周额度")
        self.body.addWidget(self.five_hour)
        self.body.addWidget(self.week)
        self.today_heading = label("今日用量", name="previewHeading")
        self.body.addWidget(self.today_heading)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.today_cost = self._stat(stats, "今日费用", "previewCost")
        self.today_tokens = self._stat(stats, "今日 Token 总数", "previewTokens")
        self.body.addLayout(stats)
        latest = QFrame()
        latest.setProperty("previewCard", True)
        last = QVBoxLayout(latest)
        last.setContentsMargins(8, 9, 8, 9)
        last.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(label("最近一次请求", muted=True))
        self.latest_status = label("", muted=True)
        row.addWidget(self.latest_status, 1)
        self.latest_cost = label("—", name="previewLatestCost")
        row.addWidget(self.latest_cost)
        last.addLayout(row)
        self.latest_message = LineLimitedText("等待用户消息", max_lines=3, fit_content=True)
        self.latest_message.setObjectName("previewMessage")
        last.addWidget(self.latest_message)
        self.latest_note = label("等待用量记录", muted=True)
        self.latest_note.setWordWrap(True)
        last.addWidget(self.latest_note)
        self.body.addWidget(latest)
        self.usage_status = label("正在读取本机记录", muted=True)
        self.usage_status.setWordWrap(True)
        self.body.addWidget(self.usage_status)
        actions = QHBoxLayout()
        self.settings_button = QPushButton("设置…")
        self.settings_button.clicked.connect(lambda: self._open("settings"))
        actions.addWidget(self.settings_button, 1)
        self.quit_button = QPushButton("退出")
        self.quit_button.clicked.connect(on_quit)
        actions.addWidget(self.quit_button)
        self.body.addLayout(actions)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.render)
        self.configure(settings, config)

    def _stat(self, row, title, name):
        frame = QFrame()
        frame.setProperty("previewCard", True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 9, 8, 9)
        layout.setSpacing(4)
        layout.addWidget(label(title, muted=True))
        value = label("—", name=name)
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        value.setWordWrap(True)
        layout.addWidget(value)
        row.addWidget(frame, 1)
        return value

    def _open(self, page):
        self.hide()
        self._on_open(page)

    def configure(self, settings, config):
        self._settings = settings.normalized()
        self._theme = config.get("theme", "system")
        self.setFixedWidth(293 if self._settings.menu_bar_preview_size == "large" else 253)
        colors = apply_theme(self, self._theme)
        self.setStyleSheet(self.styleSheet() + """
QWidget { font-size: 10px; }
QPushButton { padding: 4px 7px; border-radius: 5px; }
QProgressBar { min-height: 5px; max-height: 5px; }
QFrame#previewSurface { background: %(bg)s; border: 1px solid %(border)s; border-radius: 13px; }
QFrame[previewCard="true"] { background: %(surface)s; border: none; border-radius: 9px; }
QLabel#previewBrand { font-size: 16px; font-weight: 600; }
QLabel#previewHeading, QLabel#quotaValue { font-size: 10px; font-weight: 600; }
QLabel#previewCost, QLabel#previewTokens { font-size: 19px; font-weight: 600; }
QLabel#previewLatestCost { font-size: 18px; font-weight: 600; }
QWidget#previewMessage { font-size: 11px; }
QLabel#previewCost, QLabel#previewLatestCost { color: %(chart_cost_ink)s; }
QLabel#previewTokens { color: %(chart_tokens_ink)s; }
""" % colors)
        self.render()
        self._fit_contents()

    def _fit_contents(self):
        # Measure at the fixed preview width. QWidget.adjustSize() can cap the
        # height at two-thirds of the screen and compress wrapped rows.
        layout = self.layout()
        height = layout.totalHeightForWidth(self.width())
        self.resize(self.width(), height if height >= 0 else layout.totalSizeHint().height())

    def apply_quota(self, state):
        self._quota = state
        if self.isVisible():
            self.render()

    def apply_data(self, data):
        # Keep only what the preview needs, not the full main-window snapshot.
        self._data = {key: copy.deepcopy(data.get(key)) for key in
                      ("menu_bar_today", "today_date", "latest_request", "updated_at", "sources_complete", "scan_status")}
        latest = self._data.get("latest_request") or {}
        message = user_message_preview(latest.get("prompt_preview"))
        self.latest_message.set_text(message or ("未记录用户文字" if latest else "等待用户消息"))
        self.latest_message.setToolTip(message)
        if self.isVisible():
            self.render()

    def set_usage_loading(self, loading):
        self._loading = dict(loading)
        if self.isVisible():
            self.render()

    def render(self, now=None):
        now = now or datetime.now().astimezone()
        quota = self._quota
        show_five = self._settings.quota_scope != "week" and (
            self._settings.quota_scope == "both" or quota.five_hour.remaining_percent is not None or quota.week.remaining_percent is None)
        self.five_hour.setVisible(show_five)
        self.five_hour.render(quota.five_hour, now, self._theme)
        self.week.render(quota.week, now, self._theme)
        success = quota.last_success_at
        stale = success is not None and (now - success).total_seconds() > stale_after_seconds(self._settings.refresh_interval_seconds)
        if quota.status == QuotaStatus.ERROR:
            status = "额度读取失败 · " + quota.message
        elif quota.status == QuotaStatus.STALE or stale:
            status = "额度为上次缓存 · 等待更新"
        elif quota.status == QuotaStatus.READING:
            status = quota.message
        else:
            status = "额度已更新" + (" · " + success.astimezone().strftime("%H:%M:%S") if success else "")
        if quota.plan_type == "mock":
            status = "模拟数据 · " + status
        self.quota_status.setText(status)
        self.today_heading.setText("今日用量 · " + now.strftime("%m月%d日"))
        data = self._data or {}
        same_day = data.get("today_date") == now.date().isoformat()
        today = (data.get("menu_bar_today") or {}) if same_day else {}
        tokens, amount = today.get("tokens"), today.get("usd")
        self.today_tokens.setText(compact_number(tokens) if isinstance(tokens, int) and tokens >= 0 else "—")
        token_detail = format(tokens, ",") + " Token" if isinstance(tokens, int) and tokens >= 0 else "今日暂无计量" if same_day else "等待今日记录"
        skipped = today.get("skipped") or {}
        wholly_unpriced = amount is None and bool(skipped.get("usd"))
        self.today_cost.setText(cost_text(amount, "unpriced" if wholly_unpriced else "priced"))
        cost_detail = "等待价格数据" if wholly_unpriced else "部分未定价" if skipped.get("usd") else "USD"
        self.today_cost.setToolTip(cost_detail)
        self.today_cost.setAccessibleDescription(cost_detail)
        token_detail += "\n" + ("按已确认数据计算" if skipped.get("tokens") else "输入 + 输出，含缓存 Token")
        self.today_tokens.setToolTip(token_detail)
        self.today_tokens.setAccessibleDescription(token_detail)
        latest = data.get("latest_request") or {}
        self.latest_cost.setText(cost_text(latest.get("cost_usd"), latest.get("pricing_status", "priced")))
        request_status = latest.get("request_status", latest.get("status"))
        self.latest_status.setText(REQUEST_STATUSES.get(request_status, "未知") if latest else "暂无记录")
        colors = theme_colors(self._theme)
        self.latest_status.setStyleSheet("color: %s;" % colors["running" if request_status == "running" else "muted"])
        stamp = parse_timestamp(latest.get("timestamp"))
        detail = (stamp.astimezone().strftime("%m/%d %H:%M") + " · " if stamp else "")
        count = latest.get("model_call_count") or latest.get("call_count")
        if count:
            detail += f"{count} 次模型调用 · "
        detail += "本轮费用仍在更新" if request_status == "running" else "整轮费用"
        if latest.get("pricing_status") == "partial":
            detail += " · 部分未定价"
        self.latest_note.setText(detail if latest else "尚无用户请求记录")
        updated = parse_timestamp(data.get("updated_at"))
        if self._loading.get("error"):
            usage_status = "用量读取失败 · " + str(self._loading["error"])
        elif not data:
            usage_status = self._loading.get("stage") or "正在读取本机记录"
        elif not same_day:
            usage_status = "日期已切换 · 等待刷新今日用量"
        elif not data.get("sources_complete"):
            usage_status = "部分来源尚未同步 · 当前显示已读取数据"
        elif updated and (now - updated).total_seconds() > 120:
            usage_status = "用量为上次缓存 · " + updated.astimezone().strftime("%H:%M:%S")
        else:
            usage_status = "用量更新 " + (updated.astimezone().strftime("%H:%M:%S") if updated else "—")
        self.usage_status.setText(usage_status)

    def show_at(self, anchor):
        self.render()
        self._fit_contents()
        screen = QApplication.screenAt(anchor.center()) or QApplication.primaryScreen()
        if screen:
            self.move(popup_position(anchor, self.size(), screen.availableGeometry()))
        self.show()
        self.raise_()
        self.activateWindow()
        self.open_button.setFocus()

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and self.isVisible():
            # Popup windows do not always grow when a child wraps after the
            # first layout pass. Refit after that pass, and on live updates.
            self._fit_contents()
        return handled

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        self.last_hidden_at = time.monotonic()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)


class MenuBarController(QObject):
    def __init__(self, parent, settings: AppSettings, config, *, on_open, on_quit):
        super().__init__(parent)
        self.preview = MenuBarPreview(settings, config, on_open=on_open, on_quit=on_quit)
        self.tray = QSystemTrayIcon(menu_bar_icon(), self)
        self.tray.setToolTip("Codexio · 点击查看额度与用量")
        # macOS opens a native context menu on mouse-down if one is attached;
        # leave it unset so both clicks can open our full-size preview.
        self.tray.activated.connect(self._activated)
        self.tray.show()

    def _activated(self, reason):
        if reason not in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.Context):
            return
        if self.preview.isVisible():
            self.preview.hide()
        elif time.monotonic() - self.preview.last_hidden_at > 0.2:
            anchor = self.tray.geometry()
            if anchor.isEmpty():
                anchor = QRect(QCursor.pos(), QSize(1, 1))
            self.preview.show_at(anchor)

    def stop(self):
        self.preview.hide()
        self.tray.hide()
