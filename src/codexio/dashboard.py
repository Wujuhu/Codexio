"""The application's native main window. Data acquisition belongs to its controller."""
from __future__ import annotations

import copy
import math
import re
import uuid
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional

from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QDateEdit,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from codexio import __version__
from codexio.money import usd
from codexio.charts import UsageChart, bucket_records, compact_number, parse_timestamp, period_bounds
from codexio.activity import UsageActivity, activity_bounds
from codexio.settings import AppSettings
from codexio.durations import duration_text, duration_tooltip
from codexio.rate_limits import format_reset_time, format_reset_date
from codexio.theme import apply_theme, theme_colors
from codexio.user_requests import aggregate_user_requests, matches_call, REQUEST_STATUSES
from codexio.usage_collector import _user_preview
from codexio.usage_queries import summarize_model_calls, compare_usage
from codexio.confirmed_usage import summarize_confirmed_usage
from codexio.estimate_display import (ESTIMATE_HEADERS, estimate_amount, estimate_detail, estimate_history,
                                      method_label, select_estimates)
from codexio.analytics_config import NAVIGATION_PAGES, normalize_navigation_order, normalize_subscription_profile
from codexio.desktop_widgets import (DatePicker, DrawerHost, LedgerTable, NavigationList, PAGE_TITLES, PeriodChange, QuotaMeter,
                                    SegmentedControl, TokenComposition, WidgetStylePreview, ledger_duration_text, preview_title, tier_label, ui_icon)

PAGE_NAMES = NAVIGATION_PAGES
PAGE_LABELS = tuple(PAGE_TITLES[name] for name in PAGE_NAMES)
PERIODS = (("今日", "today"), ("近 7 天", "week"), ("近 30 天", "month"), ("全部", "all"))
PRICE_STATUS_LABELS = {"priced": "已定价", "unpriced": "未定价", "estimated": "Standard 单价", "invalid": "计量分项异常",
                       "partial": "部分未定价", "unmetered": "待计量"}
CALL_HEADERS = ["时间", "模型", "档位", "输入", "输出", "费用", "耗时", "Session ID", "来源"]
USER_REQUEST_HEADERS = CALL_HEADERS[:-1] + ["状态", "来源"]
DURATION_COLUMN = CALL_HEADERS.index("耗时")
SESSION_COLUMN = CALL_HEADERS.index("Session ID")
STATUS_COLUMN = USER_REQUEST_HEADERS.index("状态")
ESTIMATE_STATUSES = {
    "ready": ("已折算", "按已记录用量折算整周额度，金额供参考。"),
    "unattributed": ("待确认账号", "请在设置中确认这段历史记录所属的账号。"),
    "unknown_plan": ("套餐未识别", "缺少这段记录的订阅套餐信息。"),
    "unknown_limit": ("额度类型待确认", "部分请求还无法对应到周额度。"),
    "incomplete": ("用量待补齐", "等待日志同步完整后重新计算。"),
    "unpriced": ("价格待补齐", "部分请求尚无可用价格。"),
    "estimated_prices": ("参考估值", "部分调用使用参考单价，金额供参考。"),
    "collecting": ("继续采样", "等待足够的用量记录与额度变化。"),
    "insufficient": ("继续采样", "额度变化达到 2 个百分点后开始计算。"),
}


def estimate_status(value: dict) -> tuple[str, str]:
    if str(value.get("method", "")).startswith("server_"):
        label = ("较高可信" if value.get("method") == "server_aligned" else "全账号范围") if value.get("status") == "ready" else {
            "inconsistent": "数据待核对", "pending_daily": "等待补账", "missing_daily": "等待日数据",
            "mixed_scope": "额度池待确认", "unknown_plan": "套餐未识别",
        }.get(value.get("status"), "继续采样")
        return label, estimate_detail(value)
    status = value.get("status") or ("ready" if value.get("estimated_total_usd") is not None else "insufficient")
    return ESTIMATE_STATUSES.get(status, ("暂不可用", "等待完整的用量与额度数据。"))


def estimate_interval(value: dict) -> tuple[str, str]:
    start, end = parse_timestamp(value.get("start")), parse_timestamp(value.get("end"))
    if start is None or end is None:
        return "—", "采样时间未记录"
    text = start.strftime("%m/%d %H:%M") + "\n→ " + end.strftime("%m/%d %H:%M")
    return text, start.strftime("%Y/%m/%d %H:%M") + " → " + end.strftime("%Y/%m/%d %H:%M")


def fast_mode_label(record: dict) -> str:
    return tier_label(record)


def plain_label(text: object = "", *, muted: bool = False, wrap: bool = False) -> QLabel:
    label = QLabel(str(text or ""))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("muted", muted)
    label.setWordWrap(wrap)
    return label


class SessionIdLabel(QLabel):
    def __init__(self, value: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.full_value = value
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip(value)
        self.setAccessibleName(value)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def resizeEvent(self, event) -> None:
        metrics, width = self.fontMetrics(), max(1, self.width())
        if len(self.full_value) > 18:
            first = metrics.elidedText(self.full_value[:8], Qt.TextElideMode.ElideRight, width)
            last = metrics.elidedText("…" + self.full_value[-8:], Qt.TextElideMode.ElideLeft, width)
            self.setText(first + "\n" + last)
        else:
            self.setText(metrics.elidedText(self.full_value, Qt.TextElideMode.ElideMiddle, width))
        super().resizeEvent(event)


def request_cost_text(record: dict) -> str:
    status = record.get("pricing_status")
    if status == "unmetered":
        return "待计量"
    value = usd(record.get("cost_usd"))
    if status == "partial":
        return value + "\n部分未定价"
    return value


def combo(items: tuple, selected: object = None) -> QComboBox:
    widget = QComboBox()
    for label, value in items:
        widget.addItem(label, value)
    if selected is not None:
        index = widget.findData(selected)
        widget.setCurrentIndex(max(0, index))
    return widget


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    return frame, layout


def dialog_buttons(flags, parent=None):
    buttons = QDialogButtonBox(flags, parent)
    for flag, label in ((QDialogButtonBox.StandardButton.Save, "保存"),
                        (QDialogButtonBox.StandardButton.Cancel, "取消"),
                        (QDialogButtonBox.StandardButton.Close, "关闭"),
                        (QDialogButtonBox.StandardButton.Ok, "确定")):
        button = buttons.button(flag)
        if button is not None:
            button.setText(label)
    return buttons


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    for column in range(len(headers)):
        widget.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(54)
    widget.setShowGrid(False)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(False)
    widget.horizontalHeader().setHighlightSections(False)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    return widget


class CompactLogTable(QTableWidget):
    """Fill available width while keeping short columns content-sized."""

    def __init__(self, headers, parent=None):
        super().__init__(0, len(headers), parent)
        self.setObjectName("requestLogTable")
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setMinimumSectionSize(24)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._headers = []
        self._resize_pending = False
        self._column_timer = QTimer(self)
        self._column_timer.setSingleShot(True)
        self._column_timer.timeout.connect(self.fit_columns)
        self.set_headers(headers)
        self.viewport().installEventFilter(self)

    def set_headers(self, headers):
        if list(headers) == self._headers:
            return False
        self._headers = list(headers)
        self.setRowCount(0)
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        self.queue_columns()
        return True

    def queue_columns(self):
        if not self._resize_pending:
            self._resize_pending = True
            self._column_timer.start(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.queue_columns()

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self.queue_columns()
        return super().eventFilter(watched, event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, "_resize_pending") and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.queue_columns()

    def fit_columns(self):
        self._resize_pending = False
        if not self._headers:
            return
        metrics, header_metrics = self.fontMetrics(), self.horizontalHeader().fontMetrics()
        scale = max(1.0, metrics.height() / 18.0)
        bounds = {"时间": (80, 144), "模型": (76, 170), "输入": (64, 128), "输出": (56, 108),
                  "费用": (72, 140), "耗时": (70, 112), "Session ID": (92, 174), "来源": (56, 106)}
        widths, minimums = [], []
        for column, name in enumerate(self._headers):
            if name in ("Fast", "档位", "状态"):
                values = ("Fast", "Standard", "未记录", "Mixed") if name in ("Fast", "档位") else tuple(REQUEST_STATUSES.values())
                width = max(max(header_metrics.horizontalAdvance(name), *(metrics.horizontalAdvance(text) for text in values)) + 18,
                            self.sizeHintForColumn(column) + 2, self.horizontalHeader().sectionSizeHint(column))
                widths.append(width)
                minimums.append(width)
                continue
            low, high = (round(value * scale) for value in bounds[name])
            measured = max(header_metrics.horizontalAdvance(name) + 18, self.sizeHintForColumn(column) + 2)
            if name == "Session ID":
                measured = header_metrics.horizontalAdvance(name) + 18
                for row in range(min(100, self.rowCount())):
                    cell = self.cellWidget(row, column)
                    label = cell.findChild(SessionIdLabel) if cell else None
                    if label:
                        value = label.full_value
                        lines = (value[:8], "…" + value[-8:]) if len(value) > 18 else (value,)
                        measured = max(measured, max(label.fontMetrics().horizontalAdvance(line) for line in lines) + 23 + 3 + 12)
            else:
                for row in range(min(100, self.rowCount())):
                    item = self.item(row, column)
                    if item:
                        measured = max(measured, *(metrics.horizontalAdvance(line) + 16 for line in item.text().splitlines() or [""]))
            widths.append(min(high, max(low, measured)))
            minimums.append(low)
        available = max(1, self.viewport().width())
        shortage = max(0, sum(widths) - available)
        if shortage:
            room = [width - minimum for width, minimum in zip(widths, minimums)]
            total_room = sum(room)
            for index, amount in enumerate(room):
                reduction = min(amount, math.ceil(shortage * amount / total_room)) if total_room else 0
                widths[index] -= reduction
        surplus = max(0, available - sum(widths))
        flexible = [column for column, name in enumerate(self._headers) if name not in ("Fast", "状态", "来源", "耗时")]
        # Equal added space preserves the balanced gutters on both sides of Fast.
        for index, column in enumerate(flexible):
            share = surplus // (len(flexible) - index)
            widths[column] += share
            surplus -= share
        for column, width in enumerate(widths):
            self.setColumnWidth(column, int(width))
        self.verticalHeader().setDefaultSectionSize(max(54, metrics.height() * 2 + 12))


class LineLimitedText(QWidget):
    """Plain-text shaping with a hard visual line limit."""

    def __init__(self, text: str, parent: Optional[QWidget] = None, max_lines: int = 2, *, fit_content=False) -> None:
        super().__init__(parent)
        self.max_lines = max(1, int(max_lines))
        self.fit_content = fit_content
        self.text = " ".join(str(text).split())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(self.text)
        self.last_line_count = 0
        self._sync_height()

    def set_text(self, text):
        self.text = " ".join(str(text).split())
        self.setAccessibleName(self.text)
        self._sync_height()
        self.update()

    def _sync_height(self):
        if self.fit_content:
            _, lines = self.layout_lines()
            height = math.ceil(sum(line.height() for line in lines)) + 2
        else:
            height = QFontMetrics(self.font()).lineSpacing() * self.max_lines + 2
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_content:
            self._sync_height()

    def layout_lines(self, width: Optional[float] = None) -> tuple[QTextLayout, list]:
        layout = QTextLayout(self.text, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        lines = []
        layout.beginLayout()
        y = 0.0
        for _ in range(self.max_lines):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1.0, width if width is not None else self.width()))
            line.setPosition(QPointF(0, y))
            y += line.height()
            lines.append(line)
        layout.endLayout()
        self.last_line_count = len(lines)
        return layout, lines

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        layout, lines = self.layout_lines()
        for index, line in enumerate(lines):
            utf16 = self.text.encode("utf-16-le", errors="replace")
            if index == self.max_lines - 1 and line.textStart() + line.textLength() < len(utf16) // 2:
                rest = utf16[line.textStart() * 2:].decode("utf-16-le", errors="replace")
                clipped = QFontMetrics(self.font()).elidedText(rest, Qt.TextElideMode.ElideRight, self.width())
                final = QTextLayout(clipped, self.font())
                final.beginLayout()
                shaped = final.createLine()
                shaped.setLineWidth(self.width())
                final.endLayout()
                final.draw(painter, QPointF(0, line.y()))
            else:
                line.draw(painter, QPointF())
        painter.end()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.FontChange:
            self._sync_height()
        super().changeEvent(event)


TwoLineText = LineLimitedText  # Compatibility for title previews and callers.


class RequestPreviewText(QWidget):
    """Up to three bold message lines plus a separate attached-image line."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text = _user_preview(text) or "未记录用户输入"
        self.max_lines = 3
        self.setObjectName("inspectorRequestText")
        self.setAccessibleName(self.text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        match = re.search(r"(?:^|\n)(\[image\] x \d+)$", self.text)
        body = self.text[:match.start()].strip() if match else self.text
        self.body = LineLimitedText(body, max_lines=3, fit_content=True) if body else None
        self.images = LineLimitedText(match.group(1), max_lines=1, fit_content=True) if match else None
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.Bold)
        for label in (self.body, self.images):
            if label:
                label.setFont(font)
                layout.addWidget(label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class SessionTooltip(QFrame):
    def __init__(self, title: str, preview: str, parent: Optional[QWidget] = None, output_preview: str = "") -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("sessionTooltip")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        if title.lstrip().startswith(("# Files mentioned by the user", "# AGENTS.md instructions")):
            title = ""
        self.title = TwoLineText(title or "未记录会话标题", self)
        self.title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.preview = LineLimitedText("用户：" + (preview or "未记录该轮用户消息"), self, max_lines=3)
        self.preview.setStyleSheet("font-size: 14px;")
        self.output_preview = LineLimitedText("模型：" + (output_preview or "未记录本次请求的可见输出"), self, max_lines=3)
        self.output_preview.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.title)
        layout.addWidget(self.preview)
        layout.addWidget(self.output_preview)

    def show_at(self, position: QPoint) -> None:
        self.adjustSize()
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            position.setX(max(area.left(), min(position.x(), area.right() - self.width())))
            position.setY(max(area.top(), min(position.y(), area.bottom() - self.height())))
        self.move(position)
        self.show()


class SessionInfoButton(QToolButton):
    def __init__(self, record: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setText("!")
        self.setFixedSize(23, 23)
        self.setStyleSheet("QToolButton { border-radius: 11px; padding: 0; font-weight: 700; }")
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setAccessibleName("查看会话标题和该轮用户输入")
        self.record = record
        self._tip: Optional[SessionTooltip] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_tip)
        self.clicked.connect(self._show_tip)

    def _show_tip(self) -> None:
        if not self.isVisible() or self.window().isMinimized():
            return
        if self._tip is None:
            self._tip = SessionTooltip(str(self.record.get("session_title") or ""), str(self.record.get("prompt_preview") or ""), self,
                                       output_preview=str(self.record.get("output_preview") or ""))
        root = self.window()
        theme = getattr(root, "_theme", "system")
        c = theme_colors(theme)
        self._tip.setStyleSheet("QFrame#sessionTooltip { background: %s; border: 1px solid %s; border-radius: 10px; } QWidget { color: %s; }" % (c["surface"], c["border"], c["text"]))
        self._tip.show_at(self.mapToGlobal(QPoint(0, self.height() + 6)))

    def enterEvent(self, event) -> None:
        self._timer.start(140)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._timer.stop()
        if self._tip:
            self._tip.hide()
        super().leaveEvent(event)

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            self._show_tip()
            return True
        if event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self._timer.stop()
            if self._tip:
                self._tip.hide()
        return super().event(event)


def populate_request_table(view: CompactLogTable, rows: list[dict], grouped: bool = False) -> None:
    view.setUpdatesEnabled(False)
    view.setRowCount(0)
    view.setRowCount(len(rows))
    for index, row in enumerate(rows):
        stamp = parse_timestamp(row.get("timestamp"))
        time_text = stamp.strftime("%m/%d %H:%M:%S") if stamp else "—"
        if grouped:
            detail = ("未归属调用" if row.get("record_kind") == "unassigned" else
                      ("子代理 · " if row.get("is_subagent") else "") + "%d 条调用" % row.get("call_count", 0))
            time_text += "\n" + detail
        values = [time_text, row.get("model") or "未知模型", fast_mode_label(row),
                  "%s\n缓存 %s" % (format(int(row.get("input_tokens") or 0), ","), compact_number(row.get("cached_input_tokens"))),
                  format(int(row.get("output_tokens") or 0), ","), request_cost_text(row), duration_text(row), ""]
        if grouped:
            values.append(REQUEST_STATUSES.get(row.get("request_status"), "未知"))
        values.append(row.get("source_name") or row.get("source_id") or "本机")
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(str(value))
            view.setItem(index, column, item)
        session = QWidget()
        line = QHBoxLayout(session)
        line.setContentsMargins(6, 0, 6, 0)
        line.setSpacing(3)
        line.addWidget(SessionIdLabel(str(row.get("session_id") or "未记录")), 1)
        line.addWidget(SessionInfoButton(row, session))
        view.setCellWidget(index, SESSION_COLUMN, session)
        view.item(index, 0).setToolTip(("发起时间：" if grouped else "时间：") + (stamp.strftime("%Y-%m-%d %H:%M:%S %Z") if stamp else "未记录")
                                      + ("\n结束时间：" + str(row.get("ended_at") or "未记录") if grouped else "")
                                      + ("\n旧日志缺少开始标记，按首次计量时间归属。" if grouped and row.get("started_inferred") else ""))
        view.item(index, 1).setToolTip("\n".join(row.get("models") or [str(row.get("model") or "未知模型")]))
        view.item(index, 2).setToolTip("开启：Fast；关闭：Standard；混合：本轮包含不同档位；未知：日志未记录。")
        view.item(index, 3).setToolTip("输入 Token：{:,}\n缓存命中：{:,}\n缓存创建：{:,}".format(
            int(row.get("input_tokens") or 0), int(row.get("cached_input_tokens") or 0), int(row.get("cache_write_input_tokens") or 0)))
        view.item(index, 5).setToolTip(request_cost_text(row) + "\n" + str(row.get("pricing_reason") or ""))
        view.item(index, DURATION_COLUMN).setToolTip(duration_tooltip(row))
        view.item(index, view.columnCount() - 1).setToolTip("\n".join(row.get("source_names") or [str(values[-1])]))
        if grouped:
            view.item(index, STATUS_COLUMN).setToolTip(REQUEST_STATUSES.get(row.get("request_status"), "未知") + "\n" +
                (str(row.get("association_note")) if row.get("association_note") else "包含已明确关联的子代理；只有整轮结束后才显示完成。"))
    view.setUpdatesEnabled(True)
    view.queue_columns()


class RequestContent(QWidget):
    def __init__(self, record: dict, theme: str = "system", parent: Optional[QWidget] = None, compact: bool = False) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = plain_label(record.get("model") or "未知模型")
        title.setProperty("subheading" if compact else "heading", True)
        if compact:
            top = QHBoxLayout()
            top.addWidget(title, 1)
            amount = plain_label(request_cost_text(record), wrap=True)
            amount.setProperty("money", True)
            top.addWidget(amount)
            layout.addLayout(top)
        else:
            layout.addWidget(title)
        stamp = parse_timestamp(record.get("timestamp"))
        layout.addWidget(plain_label(stamp.strftime("%Y-%m-%d %H:%M:%S %Z") if stamp else "时间未记录", muted=True))
        grouped = record.get("record_kind") == "user_request"
        if grouped:
            detail = "%s · %s 条调用" % (REQUEST_STATUSES.get(record.get("request_status"), "未知"), record.get("call_count", 0))
            if record.get("subagent_count"):
                detail += " · 包含 %d 个子代理" % record["subagent_count"]
            layout.addWidget(plain_label(detail, muted=True))
        session_row = QHBoxLayout()
        session_row.addWidget(plain_label("Session ID"))
        self.session_id = QLineEdit(str(record.get("session_id") or "未记录"))
        self.session_id.setReadOnly(True)
        session_row.addWidget(self.session_id, 1)
        session_row.addWidget(SessionInfoButton(record, self))
        copy_button = QPushButton("复制")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(str(record.get("session_id") or "")))
        session_row.addWidget(copy_button)
        layout.addLayout(session_row)
        box, content = card()
        grid = QGridLayout()
        fields = [("输入 Token（含缓存）", "input_tokens"), ("缓存命中", "cached_input_tokens"),
                  ("缓存创建", "cache_write_input_tokens"), ("输出 Token", "output_tokens"),
                  ("其中推理 Token", "reasoning_output_tokens"), ("总 Token", "total_tokens")]
        if compact:
            fields = [("Fast 模式", None), ("输入 Token（含缓存）", "input_tokens"),
                      ("输出 Token", "output_tokens"), ("Total Token", "total_tokens")]
        for index, (label, key) in enumerate(fields):
            cell = QVBoxLayout()
            cell.addWidget(plain_label(label, muted=True))
            value = plain_label(fast_mode_label(record) if key is None else format(int(record.get(key) or 0), ","))
            value.setProperty("subheading", True)
            cell.addWidget(value)
            columns = 4 if compact else 3
            grid.addLayout(cell, index // columns, index % columns)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(20)
        content.addLayout(grid)
        if not compact:
            amount = plain_label(request_cost_text(record), wrap=True)
            amount.setProperty("money", True)
            content.addWidget(amount)
        if not compact:
            content.addWidget(plain_label("缓存属于输入、推理属于输出；分项不会重复计费。", muted=True))
        layout.addWidget(box)
        if compact:
            return
        form = QFormLayout()
        metadata = [("Response ID", record.get("response_id") or record.get("id")),
                    ("Turn ID", record.get("turn_id")), ("数据来源", record.get("source_name") or record.get("source_id")),
                    ("服务档位", record.get("service_tier") or "未记录"),
                    ("计价状态", PRICE_STATUS_LABELS.get(record.get("pricing_status"), record.get("pricing_status") or "未记录")),
                    ("计价说明", record.get("pricing_reason") or "未记录"),
                    ("记录质量", record.get("quality") or "未记录"),
                    ("耗时", duration_text(record)), ("HTTP 状态", "未记录")]
        if grouped:
            metadata = [("耗时", duration_text(record)), ("Turn ID", record.get("turn_id")),
                        ("结束时间", record.get("ended_at") or "未记录"),
                        ("包含模型", "、".join(record.get("models") or [])),
                        ("数据来源", "、".join(record.get("source_names") or [])),
                        ("计价状态", PRICE_STATUS_LABELS.get(record.get("pricing_status"), "未记录")),
                        ("计价说明", record.get("pricing_reason")),
                        ("关联说明", record.get("association_note") or "仅合并能够明确归属的子代理调用"),
                        ("用户", record.get("prompt_preview") or "未记录"),
                        ("模型", record.get("output_preview") or "等待可见回复")]
        for label, value in metadata:
            detail = plain_label(value or "未记录", wrap=True)
            detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label, detail)
        layout.addLayout(form)


class RequestDetails(QDialog):
    def __init__(self, record: dict, theme: str = "system", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setWindowTitle("请求详情")
        self.setMinimumWidth(620)
        self.setMinimumHeight(620)
        apply_theme(self, theme)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        content = RequestContent(record, theme, self)
        self.session_id = content.session_id
        layout.addWidget(content)
        buttons = dialog_buttons(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class UserRequestDetails(QDialog):
    def __init__(self, group: dict, records: list[dict], theme: str, parent=None, *, queries=None):
        super().__init__(parent)
        self.setWindowTitle("用户请求详情")
        self.resize(980, 780)
        self.setMinimumSize(760, 600)
        self._children = []
        self._queries = queries
        self._members = [] if queries else sorted(records, key=lambda row: str(row.get("timestamp") or ""))
        self._summary_record = group
        apply_theme(self, theme)
        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Vertical)
        summary = QScrollArea()
        summary.setWidgetResizable(True)
        content = RequestContent(group, theme, self)
        self.session_id = content.session_id
        summary.setWidget(content)
        split.addWidget(summary)
        calls = QWidget()
        calls_layout = QVBoxLayout(calls)
        calls_layout.setContentsMargins(0, 4, 0, 0)
        calls_layout.addWidget(plain_label("组成这轮请求的调用 · 双击查看明细", muted=True))
        self.call_table = CompactLogTable(CALL_HEADERS)
        calls_layout.addWidget(self.call_table)
        self._member_page = 0
        navigation = QHBoxLayout()
        count_label = plain_label("", muted=True)
        previous, following = QPushButton("上一页"), QPushButton("下一页")
        self._previous_member, self._next_member = previous, following
        navigation.addWidget(count_label, 1)
        navigation.addWidget(previous)
        navigation.addWidget(following)
        calls_layout.addLayout(navigation)

        def render_members(delta=0):
            if queries:
                result = queries.request_members(group["id"], page=self._member_page + delta, page_size=100)
                self._member_page = result["page"]
                self._members = visible = result["rows"]
                count, pages = result["total"], result["pages"]
                current = queries.request(group["id"])
                if current and current != self._summary_record:
                    self._summary_record = current
                    old_content = summary.takeWidget()
                    if old_content:
                        old_content.deleteLater()
                    content = RequestContent(current, theme, self)
                    self.session_id = content.session_id
                    summary.setWidget(content)
            else:
                count = len(self._members)
                pages = max(1, math.ceil(count / 100))
                self._member_page = max(0, min(self._member_page + delta, pages - 1))
                offset = self._member_page * 100
                visible = self._members[offset:offset + 100]
            populate_request_table(self.call_table, visible)
            count_label.setText("%d 条调用 · %d / %d 页" % (count, self._member_page + 1, pages))
            previous.setEnabled(self._member_page > 0)
            following.setEnabled(self._member_page < pages - 1)

        previous.clicked.connect(lambda: render_members(-1))
        following.clicked.connect(lambda: render_members(1))
        render_members()
        split.addWidget(calls)
        split.setSizes([400, 270])
        layout.addWidget(split, 1)

        def show_call(row, _column):
            index = row if queries else self._member_page * 100 + row
            if 0 <= index < len(self._members):
                dialog = RequestDetails(self._members[index], theme, self)
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                self._children.append(dialog)
                dialog.finished.connect(lambda *_: self._children.remove(dialog) if dialog in self._children else None)
                dialog.show()

        self.call_table.cellDoubleClicked.connect(show_call)
        buttons = dialog_buttons(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SourceEditor(QDialog):
    def __init__(self, kind: str, value: Optional[dict] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.original = dict(value or {})
        self.setWindowTitle({"local": "本机 Codex 目录", "ssh": "SSH 数据来源"}[kind])
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields: dict[str, QWidget] = {}
        definitions = {"local": [("root", "Codex 根目录", "C:\\Users\\…\\.codex")],
                       "ssh": [("name", "显示名称", "工作站"), ("host", "SSH 主机别名", "例如 my-workstation"),
                               ("root", "远端 Codex 目录", "~/.codex"), ("python", "远端 Python", "python3")]}[kind]
        for key, label, placeholder in definitions:
            field = QLineEdit(str(self.original.get(key) or ("python3" if key == "python" else "")))
            field.setPlaceholderText(placeholder)
            if key == "token":
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[key] = field
            form.addRow(label, field)
        if kind != "local":
            enabled = QCheckBox("启用此来源")
            enabled.setChecked(bool(self.original.get("enabled", True)))
            self.fields["enabled"] = enabled
            form.addRow(enabled)
        layout.addLayout(form)
        hint = "使用已配置的 SSH 密钥或 agent；保存并启用后开始读取远端计量记录。" if kind == "ssh" else "只合并计量记录；同一请求会自动去重。"
        layout.addWidget(plain_label(hint, muted=True, wrap=True))
        self.error = plain_label()
        layout.addWidget(self.error)
        buttons = dialog_buttons(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_value(self) -> dict:
        out = dict(self.original)
        for key, widget in self.fields.items():
            out[key] = widget.isChecked() if isinstance(widget, QCheckBox) else widget.value() if isinstance(widget, QSpinBox) else widget.text().strip()
        if self.kind != "local":
            out.setdefault("id", uuid.uuid4().hex)
            out["name"] = out.get("name") or out.get("host")
        return out

    def _save(self) -> None:
        value = self.result_value()
        if not value.get("root" if self.kind == "local" else "host"):
            self.error.setText("请填写目录。" if self.kind == "local" else "请填写主机地址。")
            return
        self.accept()


class HistoryAssignmentEditor(QDialog):
    """Explicit source and time range: old logs never gain account identity silently."""

    def __init__(self, records: list[dict], sources: list[dict], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("历史计量归属")
        self.setMinimumWidth(470)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label("将指定来源与日期范围的历史计量归属到当前账号，用于周额度统计。", muted=True, wrap=True))
        form = QFormLayout()
        self.source = QComboBox()
        names = {str(s.get("id") or s.get("source_id")): str(s.get("name") or s.get("source_id") or s.get("id")) for s in sources}
        for row in records:
            source = str(row.get("source_id") or "local")
            names.setdefault(source, str(row.get("source_name") or source))
        for source, name in names.items():
            self.source.addItem(name, source)
        if not self.source.count():
            self.source.addItem("本机", "local")
        stamps = [stamp for r in records if (stamp := parse_timestamp(r.get("timestamp"))) is not None]
        earliest = min(stamps).date() if stamps else datetime.now().date()
        self.start = DatePicker(QDate(earliest.year, earliest.month, earliest.day), theme=getattr(parent, "_theme", "system"))
        self.end = DatePicker(QDate.currentDate(), theme=getattr(parent, "_theme", "system"))
        form.addRow("数据来源", self.source)
        form.addRow("起始日期", self.start)
        form.addRow("结束日期（含当天）", self.end)
        layout.addLayout(form)
        self.confirm = QCheckBox("这些记录均属于当前账号")
        layout.addWidget(self.confirm)
        self.error = plain_label()
        layout.addWidget(self.error)
        buttons = dialog_buttons(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_value(self) -> dict:
        zone = datetime.now().astimezone().tzinfo
        start = datetime.combine(self.start.date().toPython(), time.min, tzinfo=zone)
        end = datetime.combine(self.end.date().toPython() + timedelta(days=1), time.min, tzinfo=zone)
        return {"source_id": self.source.currentData(), "start": start.astimezone(timezone.utc).isoformat(),
                "end": end.astimezone(timezone.utc).isoformat(), "account_key": "current"}

    def _save(self) -> None:
        if self.start.date() > self.end.date():
            self.error.setText("结束日期应晚于或等于起始日期。")
            return
        if not self.confirm.isChecked():
            self.error.setText("请确认所选记录的账号归属。")
            return
        self.accept()


def price_rate_text(value):
    return usd(value, symbol=False)


class PriceEditor(QDialog):
    def __init__(self, price: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑标准 API 基础价 · 美元 / 百万 Token")
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.model = QLineEdit(str(price.get("model") or ""))
        form.addRow("模型 ID", self.model)
        self.inputs: dict[str, QDoubleSpinBox] = {}
        self._original_rates = {key: price.get(key) for key in ("input", "cache_read", "cache_write", "output")}
        self._edited_rates = set()
        for key, label in (("input", "普通输入"), ("cache_read", "缓存读取"), ("cache_write", "缓存创建"), ("output", "输出")):
            spin = QDoubleSpinBox()
            spin.setRange(-1, 1000000)
            spin.setSpecialValueText("未定价")
            spin.setDecimals(2)
            spin.setSingleStep(0.01)
            displayed = price_rate_text(price.get(key))
            spin.setValue(-1 if displayed == "未定价" else float(displayed))
            spin.valueChanged.connect(lambda _value, field=key: self._edited_rates.add(field))
            spin.lineEdit().textEdited.connect(lambda _text, field=key: self._edited_rates.add(field))
            self.inputs[key] = spin
            form.addRow(label, spin)
        layout.addLayout(form)
        layout.addWidget(plain_label("只修改 Standard 基础价。Fast 与长上下文价格按 Codex 规则生成，并同步到所有费用。手工基础价在自动同步后保留。", muted=True, wrap=True))
        self.error = plain_label()
        layout.addWidget(self.error)
        buttons = dialog_buttons(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        if not self.model.text().strip():
            self.error.setText("请填写模型 ID。")
            return
        if any(self.inputs[key].value() < 0 for key in ("input", "output")):
            self.error.setText("请填写普通输入和输出的基础价。")
            return
        self.accept()

    def rates(self) -> dict:
        # Merely displaying a rounded rate must not rewrite the catalog value.
        return {**{key: (self._original_rates[key] if key not in self._edited_rates else
                        None if item.value() < 0 else item.value()) for key, item in self.inputs.items()},
                "service_tier": "default", "threshold": 0,
                "locked": True}


class Dashboard(QMainWindow):

    def __init__(self, settings: AppSettings, analytics_config: dict, callbacks: dict) -> None:
        super().__init__()
        self._settings = settings.normalized()
        self._config = copy.deepcopy(analytics_config)
        self._callbacks = callbacks
        self._theme = str(self._config.get("theme") or "system")
        self._data: dict = {}
        self._activity_revision = 0
        self._activity_key = None
        self._pages: dict[str, QWidget] = {}
        self._dirty_pages = set(PAGE_NAMES)
        self._config_dirty = set(PAGE_NAMES)
        self._active_page = "overview"
        self._quota_state = None
        self._update_message = None
        self._progress_message = ""
        self._usage_error = ""
        self._rendering = False
        self._saved_view_state = {}
        self._restored_pages = set()
        self._period_overrides = {}
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._queries = None
        self._query_page: dict = {}
        self._query_filters: dict = {}
        self._records: list[dict] = []
        self._records_by_id: dict[str, dict] = {}
        self._user_requests: list[dict] = []
        self._filtered_records: list[dict] = []
        self._page_number = 0
        self._page_size = 100
        self._loading = True
        self._initial_usage_done = False
        self._initial_quota_done = False
        self._initial_loading_finished = False
        self._usage_loading_seen = False
        self._usage_loading_stage = "正在加载用量数据"
        self._source_entries: list[tuple[str, int, object]] = []
        self._dialogs: list[QDialog] = []
        self._duration_timer = QTimer(self)
        self._duration_timer.setInterval(1000)
        self._duration_timer.timeout.connect(self._refresh_duration_cells)
        self._quota_widgets = {}
        self._inspected_record = None
        self._inspector_origin = None
        self._log_preview_dismissed = False
        self._comparison_cache = {}
        self._tier_preferences = {"user_request": "", "model_call": ""}
        self._last_log_mode = "user_request"
        self.setWindowTitle("Codexio")
        self.resize(1280, 850)
        self.setMinimumSize(920, 660)
        self._build()
        self.config_updated(self._config)
        self._loading = False
        self._apply_theme()
        try:
            QApplication.instance().styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        except AttributeError:
            pass

    def _callback(self, key: str, *args) -> None:
        callback = self._callbacks.get(key)
        if callable(callback):
            callback(*args)

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("dashboardRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(180)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 20, 12, 16)
        side.setSpacing(14)
        from codexio.app_icon import render_app_pixmap
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 0, 0, 0)
        brand_row.setSpacing(4)
        brand_icon = QLabel()
        brand_icon.setObjectName("brandIcon")
        brand_icon.setPixmap(render_app_pixmap(32))
        brand_icon.setFixedSize(32, 32)
        brand_icon.setAccessibleName("Codexio")
        brand_row.addWidget(brand_icon)
        brand = plain_label("Codexio")
        brand.setObjectName("brandName")
        brand_row.addWidget(brand, 1)
        side.addLayout(brand_row)
        self._search_button = QPushButton("搜索")
        self._search_button.setObjectName("navigationSearch")
        self._search_button.setToolTip("搜索请求、会话或 ID（Ctrl+K）")
        self._search_button.clicked.connect(self._focus_request_search)
        side.addWidget(self._search_button)
        self._search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._search_shortcut.activated.connect(self._focus_request_search)
        self._navigation = NavigationList()
        self._navigation.set_order(self._config.get("navigation_order"))
        self._navigation.page_requested.connect(self.open_page)
        self._navigation.order_changed.connect(self._navigation_reordered)
        side.addWidget(self._navigation, 1)
        self._sidebar_status = plain_label("正在读取本机记录", muted=True, wrap=True)
        self._sidebar_status.setObjectName("sidebarStatus")
        side.addWidget(self._sidebar_status)
        self._account_button = QPushButton("个人订阅")
        self._account_button.setObjectName("accountButton")
        self._account_button.clicked.connect(lambda: self.open_page("subscription"))
        side.addWidget(self._account_button)
        layout.addWidget(sidebar)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 10, 10, 10)
        surface = QFrame()
        surface.setObjectName("contentSurface")
        main = QVBoxLayout(surface)
        main.setContentsMargins(26, 22, 8, 12)
        main.setSpacing(18)
        outer.addWidget(surface)
        layout.addLayout(outer, 1)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 18, 0)
        self._page_title = plain_label("概览")
        self._page_title.setProperty("heading", True)
        header.addWidget(self._page_title, 1)
        self._widget_toggle = QPushButton("悬浮窗")
        self._widget_toggle.setCheckable(True)
        self._widget_toggle.setProperty("quiet", True)
        self._widget_toggle.toggled.connect(self._toggle_widget)
        header.addWidget(self._widget_toggle)
        self._refresh_button = QPushButton()
        self._refresh_button.setAccessibleName("刷新数据")
        self._refresh_button.setToolTip("刷新额度与用量")
        self._refresh_button.setProperty("quiet", True)
        self._refresh_button.clicked.connect(lambda: self._callback("refresh"))
        header.addWidget(self._refresh_button)
        main.addLayout(header)
        self._startup_banner, startup_layout = card()
        startup_row = QHBoxLayout()
        self._startup_message = plain_label("正在加载用量数据")
        startup_row.addWidget(self._startup_message, 1)
        self._startup_progress = QProgressBar()
        self._startup_progress.setRange(0, 0)
        self._startup_progress.setTextVisible(False)
        self._startup_progress.setMinimumWidth(180)
        self._startup_progress.setMaximumWidth(280)
        startup_row.addWidget(self._startup_progress)
        startup_layout.addLayout(startup_row)
        main.addWidget(self._startup_banner)
        self._stack = QStackedWidget()
        for _name in PAGE_NAMES:
            self._stack.addWidget(QWidget())
        main.addWidget(self._stack, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 18, 0)
        self._footer = plain_label("更新时间 —", muted=True)
        self._footer.setObjectName("statusText")
        footer.addWidget(self._footer, 1)
        self._fee_caption = plain_label("API 基础价 × Codex 倍率", muted=True)
        self._fee_caption.setObjectName("statusText")
        footer.addWidget(self._fee_caption)
        main.addLayout(footer)

    def _navigation_reordered(self, order):
        self._config["navigation_order"] = normalize_navigation_order(order)
        self._navigation.select_page(self._active_page)
        self._navigation.set_theme(self._theme)
        self._callback("config", copy.deepcopy(self._config))

    def _focus_request_search(self):
        self.open_page("logs")
        self._log_search.setFocus()

    def _page(self, scroll: bool = False) -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        content.setObjectName("page")
        inner = QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(18)
        if not scroll:
            return content, inner
        # Keep the reading width while the scrollbar uses the outer right gutter.
        inner.setContentsMargins(0, 0, 18, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(content)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return area, inner

    def _build_overview(self) -> QWidget:
        page, layout = self._page(scroll=True)
        page.setObjectName("overviewScroll")
        top = QHBoxLayout()
        top.setSpacing(14)
        meters = {}
        for key, title in (("five_hour", "5 小时额度"), ("week", "本周额度")):
            meter = QuotaMeter(title)
            meters[key] = meter
            top.addWidget(meter, 1)
        self._token_composition = TokenComposition()
        top.addWidget(self._token_composition, 1)
        self._quota_widgets["overview"] = meters
        layout.addLayout(top)
        self._overview_quota_note = plain_label("", muted=True, wrap=True)
        self._overview_quota_note.hide()
        layout.addWidget(self._overview_quota_note)
        controls = QHBoxLayout()
        self._overview_period_label = plain_label("今日用量", muted=True)
        controls.addWidget(self._overview_period_label, 1)
        self._overview_period = SegmentedControl(PERIODS, "today")
        self._overview_period.changed.connect(self._overview_period_changed)
        controls.addWidget(self._overview_period)
        layout.addLayout(controls)
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self._overview_cost = plain_label("—")
        self._overview_tokens = plain_label("—")
        self._overview_calls = plain_label("—")
        self._overview_comparisons = {}
        for key, label, value in (("usd", "API 等价费用", self._overview_cost),
                                 ("tokens", "Total Token", self._overview_tokens),
                                 ("requests", "模型调用", self._overview_calls)):
            box, content = card()
            content.setSpacing(9)
            content.addWidget(plain_label(label, muted=True))
            value.setProperty("metric", True)
            content.addWidget(value)
            change = PeriodChange()
            self._overview_comparisons[key] = change
            content.addWidget(change)
            metrics.addWidget(box, 1)
        layout.addLayout(metrics)
        graph, content = card()
        row = QHBoxLayout()
        row.addWidget(plain_label("用量趋势"), 1)
        content.addLayout(row)
        self._overview_chart = UsageChart()
        self._overview_chart.set_compact(True)
        self._overview_chart.set_enabled_series({"usd", "tokens"})
        self._overview_chart.bucket_clicked.connect(self._chart_bucket_open)
        content.addWidget(self._overview_chart)
        more = QPushButton("展开趋势")
        more.setProperty("quiet", True)
        more.clicked.connect(lambda: self.open_page("trends", self._overview_period.value()))
        content.addWidget(more, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(graph)
        self._latest_box, self._latest_content = card()
        heading = QHBoxLayout()
        heading.addWidget(plain_label("最近请求"), 1)
        all_requests = QPushButton("查看全部")
        all_requests.setProperty("quiet", True)
        all_requests.clicked.connect(lambda: self.open_page("logs", self._overview_period.value()))
        heading.addWidget(all_requests)
        self._latest_content.addLayout(heading)
        self._recent_table = LedgerTable(compact=True)
        self._recent_table.setFixedHeight(270)
        self._recent_table.cellClicked.connect(self._open_recent_request)
        self._recent_empty = plain_label("暂无可识别的用户请求", muted=True)
        self._latest_content.addWidget(self._recent_table)
        self._latest_content.addWidget(self._recent_empty)
        self._recent_rows = []
        self._latest_record = None
        layout.addWidget(self._latest_box)
        return page

    def _build_trends(self) -> QWidget:
        page, layout = self._page(scroll=True)
        controls = QHBoxLayout()
        self._trend_period = combo(PERIODS + (("自选日期", "custom"),), "today")
        self._granularity = combo((("每小时", "hour"), ("每天", "day"), ("每周", "week")), "hour")
        self._trend_model = combo((("全部模型", ""),))
        self._trend_model.setMinimumWidth(170)
        self._trend_model.setMaximumWidth(250)
        self._trend_model.setEnabled(False)
        self._trend_period.currentIndexChanged.connect(self._trend_period_changed)
        self._granularity.currentIndexChanged.connect(self._update_trends)
        self._trend_model.currentIndexChanged.connect(self._update_trends)
        controls.addWidget(self._trend_period)
        controls.addWidget(self._granularity)
        controls.addWidget(self._trend_model)
        controls.addStretch()
        controls.addWidget(plain_label("点击图例选择曲线 · 悬停查看数值", muted=True))
        layout.addLayout(controls)
        dates = QHBoxLayout()
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setSpacing(8)
        self._trend_start = DatePicker(QDate.currentDate().addDays(-6), theme=self._theme)
        self._trend_end = DatePicker(QDate.currentDate(), theme=self._theme)
        for widget in (self._trend_start, self._trend_end):
            widget.dateChanged.connect(self._update_trends)
        dates.addWidget(plain_label("从"))
        dates.addWidget(self._trend_start)
        dates.addWidget(plain_label("至"))
        dates.addWidget(self._trend_end)
        dates.addStretch()
        self._trend_date_row = QWidget()
        self._trend_date_row.setLayout(dates)
        self._trend_date_row.hide()
        layout.addWidget(self._trend_date_row)
        graph, content = card()
        self._trend_chart = UsageChart()
        content.addWidget(self._trend_chart)
        graph.setMinimumHeight(380)
        graph.setMaximumHeight(500)
        layout.addWidget(graph, 1)
        self._trend_note = plain_label("", muted=True, wrap=True)
        self._trend_note.hide()
        layout.addWidget(self._trend_note)
        self._trend_metrics_box = QWidget()
        metrics = QHBoxLayout(self._trend_metrics_box)
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setSpacing(16)
        self._trend_metric_values, self._trend_comparisons = {}, {}
        for key, title in (("usd", "API 等价费用"), ("tokens", "Total Token"), ("requests", "模型调用")):
            box, content = card()
            content.setSpacing(9)
            content.addWidget(plain_label(title, muted=True))
            value = plain_label("—")
            value.setProperty("metric", True)
            content.addWidget(value)
            change = PeriodChange()
            content.addWidget(change)
            self._trend_metric_values[key], self._trend_comparisons[key] = value, change
            metrics.addWidget(box, 1)
        layout.addWidget(self._trend_metrics_box)
        activity, content = card()
        heading = QHBoxLayout()
        heading.addWidget(plain_label("每日 Token · 近一年"), 1)
        self._activity_range = plain_label("", muted=True)
        self._activity_range.setStyleSheet("font-size: 11px;")
        heading.addWidget(self._activity_range)
        content.addLayout(heading)
        self._activity_summary = plain_label("等待用量记录", muted=True)
        content.addWidget(self._activity_summary)
        self._activity_chart = UsageActivity()
        self._activity_chart.day_clicked.connect(self._activity_day_open)
        content.addWidget(self._activity_chart)
        layout.addWidget(activity)
        return page

    def _build_logs(self) -> QWidget:
        page, layout = self._page()
        mode = QHBoxLayout()
        self._log_mode = combo((("按用户请求", "user_request"), ("按模型调用", "model_call")), "user_request")
        self._log_mode.setMaximumWidth(180)
        self._log_mode.currentIndexChanged.connect(self._filters_changed)
        mode.addWidget(self._log_mode)
        mode.addStretch()
        self._log_period = combo(PERIODS + (("自选日期", "custom"),), "today")
        self._log_period.setMaximumWidth(160)
        mode.addWidget(self._log_period)
        layout.addLayout(mode)
        controls = QHBoxLayout()
        controls.setSpacing(10)
        self._log_search = QLineEdit()
        self._log_search.setPlaceholderText("搜索输入、会话或 ID")
        self._log_search.setClearButtonEnabled(True)
        self._log_search.setMaximumWidth(340)
        self._log_search_timer = QTimer(self)
        self._log_search_timer.setSingleShot(True)
        self._log_search_timer.setInterval(220)
        self._log_search_timer.timeout.connect(self._filters_changed)
        self._log_search.textChanged.connect(lambda _: self._log_search_timer.start())
        controls.addWidget(self._log_search, 1)
        controls.addStretch()
        self._log_source = combo((("全部来源", ""),))
        self._log_model = combo((("全部模型", ""),))
        self._log_tier = combo((("全部档位", ""), ("Fast", "priority"), ("Standard", "default"), ("Mixed", "mixed")))
        for field in (self._log_source, self._log_model, self._log_tier):
            field.setMinimumWidth(105)
            field.setMaximumWidth(185)
            controls.addWidget(field)
        for field in (self._log_period, self._log_source, self._log_model, self._log_tier):
            field.currentIndexChanged.connect(self._filters_changed)
        layout.addLayout(controls)
        dates = QHBoxLayout()
        dates.setContentsMargins(0, 0, 0, 0)
        dates.setSpacing(8)
        self._date_start = DatePicker(QDate.currentDate(), theme=self._theme)
        self._date_end = DatePicker(QDate.currentDate(), theme=self._theme)
        for field in (self._date_start, self._date_end):
            field.dateChanged.connect(self._filters_changed)
        dates.addWidget(plain_label("从"))
        dates.addWidget(self._date_start)
        dates.addWidget(plain_label("至"))
        dates.addWidget(self._date_end)
        dates.addStretch()
        self._date_row = QWidget()
        self._date_row.setLayout(dates)
        self._date_row.hide()
        layout.addWidget(self._date_row)
        self._log_table = LedgerTable()
        self._log_table.cellClicked.connect(self._inspect_log_row)
        self._log_table.cellActivated.connect(self._inspect_log_row)
        inspector = self._create_inspector()
        self._log_drawer = DrawerHost(self._log_table, inspector)
        self._log_canvas, records_layout = card()
        self._log_canvas.setObjectName("logCanvas")
        records_layout.setContentsMargins(22, 20, 4, 20)
        records_layout.addWidget(self._log_drawer, 1)
        layout.addWidget(self._log_canvas, 1)
        pagination = QHBoxLayout()
        pagination.setContentsMargins(0, 0, 18, 0)
        self._log_count = plain_label("暂无请求", muted=True)
        pagination.addWidget(self._log_count, 1)
        self._previous = QPushButton("上一页")
        self._next = QPushButton("下一页")
        self._page_indicator = plain_label("1 / 1", muted=True)
        self._previous.clicked.connect(lambda: self._change_page(-1))
        self._next.clicked.connect(lambda: self._change_page(1))
        pagination.addWidget(self._previous)
        pagination.addWidget(self._page_indicator)
        pagination.addWidget(self._next)
        self._log_pagination = QWidget()
        self._log_pagination.setLayout(pagination)
        records_layout.addWidget(self._log_pagination)
        return page

    def _build_pricing(self) -> QWidget:
        page, layout = self._page()
        box, content = card()
        row = QHBoxLayout()
        text = QVBoxLayout()
        heading = plain_label("模型价格（美元 / 1M Token）")
        heading.setProperty("subheading", True)
        text.addWidget(heading)
        self._price_status = plain_label("内置基础价 · 等待同步状态", muted=True, wrap=True)
        text.addWidget(self._price_status)
        row.addLayout(text, 1)
        self._auto_sync = QCheckBox("自动同步")
        self._auto_sync.toggled.connect(self._set_auto_sync)
        row.addWidget(self._auto_sync)
        sync = QPushButton("立即同步")
        sync.setProperty("primary", True)
        sync.clicked.connect(lambda: self._callback("sync_prices"))
        row.addWidget(sync)
        content.addLayout(row)
        content.addWidget(plain_label("Fast：Astra / GPT-5.6 / GPT-5.5 ×2.5；GPT-5.4 ×2。长文（>272K）：输入与缓存 ×2、输出 ×1.5；Astra 不加价。", muted=True, wrap=True))
        layout.addWidget(box)
        controls = QHBoxLayout()
        self._price_search = QLineEdit()
        self._price_search.setPlaceholderText("搜索模型…")
        self._price_search.textChanged.connect(self._update_prices)
        controls.addWidget(self._price_search, 1)
        add = QPushButton("添加基础价")
        add.clicked.connect(lambda: self._edit_price({}))
        self._edit_base_price = QPushButton("编辑基础价")
        self._edit_base_price.clicked.connect(self._edit_selected_price)
        self._reset_base_price = QPushButton("恢复自动基础价")
        self._reset_base_price.clicked.connect(self._reset_selected_price)
        self._selected_price_model = None
        self._selected_price_key = None
        self._edit_base_price.setEnabled(False)
        self._reset_base_price.setEnabled(False)
        for button in (add, self._edit_base_price, self._reset_base_price):
            controls.addWidget(button)
        layout.addLayout(controls)
        self._price_table = table(["模型", "计价条件", "输入", "缓存读取", "缓存创建", "输出"])
        self._price_table.setObjectName("pricingTable")
        self._price_table.verticalHeader().setDefaultSectionSize(50)
        self._price_table.horizontalHeader().setStretchLastSection(False)
        self._price_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._price_table.itemSelectionChanged.connect(self._price_selection_changed)
        self._price_table.cellDoubleClicked.connect(lambda *_: self._edit_selected_price())
        layout.addWidget(self._price_table, 1)
        self._price_empty = plain_label("没有匹配的模型", muted=True)
        self._price_empty.hide()
        layout.addWidget(self._price_empty)
        layout.addWidget(plain_label("普通 Standard 使用 OpenAI API 标准价；Fast 与长上下文沿用 Codex 换算规则。", muted=True, wrap=True))
        return page

    def _build_settings(self) -> QWidget:
        page, layout = self._page()
        body = QHBoxLayout()
        body.setSpacing(24)
        self._settings_sections = QListWidget()
        self._settings_sections.setObjectName("settingsSections")
        self._settings_sections.setFixedWidth(118)
        self._settings_sections.addItems(["外观", "悬浮窗", "数据来源", "应用"])
        self._settings_stack = QStackedWidget()
        body.addWidget(self._settings_sections)
        body.addWidget(self._settings_stack, 1)
        self._settings_sections.currentRowChanged.connect(self._settings_stack.setCurrentIndex)
        self._setting_widgets = {}
        def section(title, description):
            widget, inner = self._page(scroll=True)
            label = plain_label(title)
            label.setProperty("subheading", True)
            inner.addWidget(label)
            if description:
                inner.addWidget(plain_label(description, muted=True, wrap=True))
            self._settings_stack.addWidget(widget)
            return inner
        appearance = section("外观", "设置主界面的阅读环境。")
        form = QFormLayout()
        form.setVerticalSpacing(18)
        self._theme_combo = combo((("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")), self._theme)
        self._theme_combo.setMaximumWidth(220)
        self._theme_combo.currentIndexChanged.connect(self._preview_theme)
        form.addRow("主界面主题", self._theme_combo)
        self._show_log_source = QCheckBox("显示来源列")
        self._show_log_source.setChecked(self._config.get("show_log_source") is True)
        form.addRow("请求日志", self._show_log_source)
        appearance.addLayout(form)
        appearance.addWidget(plain_label("导航栏可直接拖动排序，概览固定第一。", muted=True, wrap=True))
        appearance.addStretch()
        floating = section("悬浮窗", "保留当前外观与停靠行为，保存后应用。")
        form = QFormLayout()
        form.setVerticalSpacing(13)
        fields = [("display_mode", "显示模式", (("始终置顶", "top"), ("桌面底层", "bottom"))),
                  ("visual_style", "悬浮窗样式", (("经典", "classic"), ("双环", "rings"), ("卡片", "tiles"), ("紧凑", "compact"), ("极简", "minimal"), ("光球", "orb"))),
                  ("quota_scope", "额度范围", (("自动", "auto"), ("5 小时与周额度", "both"), ("仅周额度", "week"))),
                  ("refresh_interval_seconds", "额度刷新", (("30 秒", 30), ("1 分钟", 60), ("5 分钟", 300))),
                  ("dock_edge", "贴边停靠", (("不贴边", "none"), ("顶部", "top"), ("底部", "bottom"), ("左侧", "left"), ("右侧", "right")))]
        for key, label, choices in fields:
            field = combo(choices, getattr(self._settings, key))
            field.setMaximumWidth(260)
            self._setting_widgets[key] = field
            form.addRow(label, field)
        for key, label in (("background_transparent", "背景透明"), ("show_border", "显示边框")):
            field = QCheckBox()
            field.setAccessibleName(label)
            field.setChecked(bool(getattr(self._settings, key)))
            self._setting_widgets[key] = field
            form.addRow(label, field)
        opacity = QSpinBox()
        opacity.setRange(0, 100)
        opacity.setSuffix(" %")
        opacity.setValue(self._settings.background_opacity)
        opacity.setMaximumWidth(180)
        self._setting_widgets["background_opacity"] = opacity
        form.addRow("透明度", opacity)
        border = QLineEdit(self._settings.border_color)
        border.setMaximumWidth(260)
        self._setting_widgets["border_color"] = border
        form.addRow("边框颜色", border)
        floating.addLayout(form)
        self._widget_preview = WidgetStylePreview()
        floating.addWidget(self._widget_preview)
        for key in ("visual_style", "quota_scope"):
            self._setting_widgets[key].currentIndexChanged.connect(self._preview_widget)
        for key in ("background_transparent", "show_border"):
            self._setting_widgets[key].toggled.connect(self._preview_widget)
        opacity.valueChanged.connect(self._preview_widget)
        border.textChanged.connect(self._preview_widget)
        self._preview_widget()
        floating.addStretch()
        sources = section("数据来源", "本机与远程来源分别读取，原始记录保持不变。")
        self._source_list = QListWidget()
        self._source_list.setMinimumHeight(150)
        self._source_list.itemDoubleClicked.connect(lambda *_: self._edit_source())
        sources.addWidget(self._source_list)
        buttons = QHBoxLayout()
        for kind, label in (("local", "+ 本机目录"), ("ssh", "+ SSH")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, source_kind=kind: self._edit_source(source_kind))
            buttons.addWidget(button)
        edit = QPushButton("编辑")
        edit.clicked.connect(lambda: self._edit_source())
        buttons.addWidget(edit)
        remove = QPushButton("移除")
        remove.clicked.connect(self._remove_source)
        buttons.addWidget(remove)
        sources.addLayout(buttons)
        self._sources_status = plain_label("", muted=True, wrap=True)
        sources.addWidget(self._sources_status)
        codex = QLineEdit(self._settings.codex_path or "")
        codex.setPlaceholderText("自动发现 Codex")
        self._setting_widgets["codex_path"] = codex
        sources.addWidget(plain_label("Codex 可执行文件", muted=True))
        sources.addWidget(codex)
        self._account_since = plain_label("", muted=True, wrap=True)
        sources.addWidget(self._account_since)
        maintenance = QHBoxLayout()
        assign = QPushButton("历史归属")
        assign.clicked.connect(self._assign_history)
        maintenance.addWidget(assign)
        rescan = QPushButton("重新扫描")
        rescan.clicked.connect(lambda: self._callback("rescan"))
        maintenance.addWidget(rescan)
        maintenance.addStretch()
        sources.addLayout(maintenance)
        sources.addStretch()
        updates = section("应用", "Codexio " + __version__)
        self._auto_update = QCheckBox("自动下载更新")
        self._auto_update.toggled.connect(self._set_auto_update)
        updates.addWidget(self._auto_update)
        self._check_update = QPushButton("检查并更新")
        self._check_update.clicked.connect(lambda: self._callback("check_update"))
        updates.addWidget(self._check_update, alignment=Qt.AlignmentFlag.AlignLeft)
        self._update_status = plain_label("", muted=True, wrap=True)
        updates.addWidget(self._update_status)
        self._server_estimates_enabled = QCheckBox("用服务端多日数据估算周额度")
        updates.addWidget(self._server_estimates_enabled)
        updates.addWidget(plain_label("服务端日数据允许延迟，补齐后自动重算；无须每天 08:00 在线。", muted=True, wrap=True))
        updates.addStretch()
        layout.addLayout(body, 1)
        save_row = QHBoxLayout()
        self._settings_message = plain_label("", muted=True)
        save_row.addWidget(self._settings_message, 1)
        save = QPushButton("保存设置")
        save.setProperty("primary", True)
        save.clicked.connect(self._save_settings)
        save_row.addWidget(save)
        layout.addLayout(save_row)
        self._settings_sections.setCurrentRow(0)
        return page

    @staticmethod
    def _control_value(widget):
        if isinstance(widget, SegmentedControl):
            return widget.value()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QDateEdit):
            return widget.date().toString(Qt.DateFormat.ISODate)
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QSpinBox):
            return widget.value()
        return widget.text()

    @staticmethod
    def _restore_control(widget, value):
        blocked = widget.blockSignals(True)
        try:
            if isinstance(widget, SegmentedControl):
                widget.set_value(value)
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QDateEdit):
                date = QDate.fromString(str(value), Qt.DateFormat.ISODate)
                if date.isValid():
                    widget.setDate(date)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            else:
                widget.setText(str(value or ""))
        finally:
            widget.blockSignals(blocked)

    def _view_controls(self, name):
        names = {
            "overview": ("_overview_period",),
            "subscription": (),
            "logs": ("_log_mode", "_log_period", "_log_source", "_log_model", "_log_tier", "_log_search", "_date_start", "_date_end"),
            "trends": ("_trend_period", "_granularity", "_trend_model", "_trend_start", "_trend_end"),
            "pricing": ("_price_search",),
        }.get(name, ())
        return {key: getattr(self, key) for key in names}

    def capture_view_state(self) -> dict:
        state = copy.deepcopy(self._saved_view_state)
        for name in self._pages:
            if name not in self._restored_pages:
                continue
            value = {key: self._control_value(widget) for key, widget in self._view_controls(name).items()}
            if name == "logs":
                selected = self._log_table.currentRow()
                rows = self._rendered_log_rows or []
                value.update(page=self._page_number, scroll=self._log_table.verticalScrollBar().value(),
                             selected=rows[selected].get("id") if 0 <= selected < len(rows) else None,
                             preview_dismissed=self._log_preview_dismissed)
            elif name == "settings":
                # Preserve unsaved edits only; unchanged controls should reflect
                # fresh floating-window settings when the page is recreated.
                draft = {}
                for key, widget in self._setting_widgets.items():
                    current = self._control_value(widget)
                    saved = getattr(self._settings, key)
                    if isinstance(widget, QLineEdit):
                        saved = saved or ""
                    if current != saved:
                        draft[key] = current
                value["draft"] = draft
                theme = self._theme_combo.currentData()
                if theme != self._config.get("theme", "system"):
                    value["theme_draft"] = theme
                if self._show_log_source.isChecked() != (self._config.get("show_log_source") is True):
                    value["show_log_source_draft"] = self._show_log_source.isChecked()
            state[name] = value
        return state

    def restore_view_state(self, state: dict) -> None:
        self._saved_view_state = copy.deepcopy(state) if isinstance(state, dict) else {}

    def _restore_page_state(self, name: str) -> None:
        if name in self._restored_pages:
            return
        self._restored_pages.add(name)
        state = self._saved_view_state.get(name, {})
        for key, widget in self._view_controls(name).items():
            if key in state:
                self._restore_control(widget, state[key])
        if name == "logs":
            self._page_number = max(0, int(state.get("page", 0)))
            self._saved_log_scroll = int(state.get("scroll", 0))
            self._saved_log_selection = state.get("selected")
            self._log_preview_dismissed = state.get("preview_dismissed") is True
            self._date_row.setVisible(self._log_period.currentData() == "custom")
            self._last_log_mode = self._log_mode.currentData()
            self._sync_tier_options()
        elif name == "trends":
            self._trend_date_row.setVisible(self._trend_period.currentData() == "custom")
        elif name == "settings":
            for key, value in state.get("draft", {}).items():
                if key in self._setting_widgets:
                    self._restore_control(self._setting_widgets[key], value)
            if "theme_draft" in state:
                self._restore_control(self._theme_combo, state["theme_draft"])
            if "show_log_source_draft" in state:
                self._restore_control(self._show_log_source, state["show_log_source_draft"])

    def _apply_requested_period(self, name: str) -> None:
        period = self._period_overrides.pop(name, None)
        if period is None:
            return
        if name == "overview":
            self._overview_period.set_value(period)
            return
        field = self._log_period if name == "logs" else self._trend_period
        self._restore_control(field, period)
        if name == "logs":
            self._page_number = 0
            self._log_preview_dismissed = False
            self._date_row.setVisible(field.currentData() == "custom")
        else:
            default = {"today": "hour", "week": "day", "month": "day", "all": "week"}.get(period)
            if default:
                self._restore_control(self._granularity, default)
            self._trend_date_row.setVisible(field.currentData() == "custom")

    def _sync_duration_timer(self) -> None:
        running = self._page_is_active("logs") and any(row.get("duration_running") for row in
                                                        (getattr(self, "_rendered_log_rows", None) or []))
        if running:
            if not self._duration_timer.isActive():
                self._duration_timer.start()
            self._refresh_duration_cells()
        else:
            self._duration_timer.stop()

    def _refresh_duration_cells(self) -> None:
        if not self._page_is_active("logs"):
            self._duration_timer.stop()
            return
        now = datetime.now(timezone.utc)
        for index, row in enumerate(getattr(self, "_rendered_log_rows", None) or []):
            if row.get("duration_running"):
                column = self._log_table.duration_column
                item = self._log_table.item(index, column)
                if item is not None:
                    text = ledger_duration_text(row, now)
                    if item.text() != text:
                        item.setText(text)
                        item.setToolTip(duration_text(row, now) + "\n" + duration_tooltip(row))
                        if self._log_table.fontMetrics().horizontalAdvance(text) + 16 > self._log_table.columnWidth(column):
                            self._log_table.queue_columns()
        if self._inspected_record and getattr(self, "_inspector_duration", None) is not None:
            self._inspector_duration.setText(duration_text(self._inspected_record, now))

    def _page_is_active(self, name: str) -> bool:
        return (not self._loading and self._active_page == name and name in self._pages
                and self.isVisible() and not self.isMinimized())

    def _ensure_page(self, name: str) -> None:
        if name in self._pages:
            return
        old_loading = self._loading
        self._loading = True
        try:
            page = getattr(self, "_build_" + name)()
            index = PAGE_NAMES.index(name)
            placeholder = self._stack.widget(index)
            self._stack.removeWidget(placeholder)
            placeholder.deleteLater()
            self._stack.insertWidget(index, page)
            self._pages[name] = page
            self._sync_config_controls(name)
        finally:
            self._loading = old_loading
        self._dirty_pages.add(name)
        self._apply_theme()

    def _refresh_visible(self) -> None:
        name = self._active_page
        if not self._page_is_active(name) or self._rendering:
            return
        self._rendering = True
        try:
            self._refresh_status()
            if name in self._config_dirty:
                self._sync_config_controls(name)
            if name not in self._dirty_pages:
                return
            if name == "overview":
                self._restore_page_state(name)
                self._apply_requested_period(name)
                if self._data:
                    self._render_overview()
                if self._quota_state is not None:
                    self._render_quota(self._quota_state)
                if self._usage_error and not self._data:
                    for value in (self._overview_tokens, self._overview_calls):
                        value.setText("—")
                    self._overview_cost.setText("未加载")
                    for key, change in self._overview_comparisons.items():
                        change.set_comparison(None, key, self._theme)
                    self._recent_empty.setText("请求记录加载失败")
                    self._recent_empty.show()
                    self._recent_table.hide()
            elif name == "subscription":
                self._restore_page_state(name)
                self._render_subscription()
                self._render_quota(self._quota_state)
            elif name == "logs":
                self._update_log_filter_options()
                self._restore_page_state(name)
                self._apply_requested_period(name)
                self._filter_records(reset_page=False)
            elif name == "trends":
                self._update_trend_filter_options()
                self._restore_page_state(name)
                self._apply_requested_period(name)
                self._update_trends()
            elif name == "pricing":
                self._restore_page_state(name)
                self._update_prices()
            else:
                self._restore_page_state(name)
                self._preview_widget()
                self._update_sources_status()
                if self._update_message:
                    message, busy = self._update_message
                    self._update_status.setText(message)
                    self._check_update.setEnabled(not busy)
            self._dirty_pages.discard(name)
        finally:
            self._rendering = False

    def _refresh_status(self) -> None:
        scan = self._data.get("scan_status")
        scan_text = str(scan.get("message") or scan.get("status") or "") if isinstance(scan, dict) else str(scan or "")
        record_count = self._data.get("summaries", {}).get("all", {}).get("requests", 0) if self._queries else len(self._records)
        self._sidebar_status.setText(scan_text or "%s 条调用记录" % format(record_count, ","))
        updated = parse_timestamp(self._data.get("updated_at"))
        self._footer.setText("更新时间 " + (updated.strftime("%H:%M:%S") if updated else "—"))
        if self._progress_message:
            self._sidebar_status.setText(self._progress_message)
        if self._usage_error:
            self._sidebar_status.setText(self._usage_error)
        self._account_button.setText("个人订阅\n" + self._profile_plan_text().replace("&", "&&"))
        self._update_startup_progress()

    def set_update_status(self, message: str, busy: bool = False) -> None:
        self._update_message = message, busy
        self._dirty_pages.add("settings")
        self._refresh_visible()

    def apply_data(self, data: dict) -> None:
        self._data = data if isinstance(data, dict) else {}
        self._activity_revision += 1
        self._comparison_cache.clear()
        query_path = self._data.get("query_path")
        if query_path:
            from codexio.usage_queries import UsageQueries
            self._queries = UsageQueries(query_path)
            self._records, self._records_by_id, self._user_requests = [], {}, []
        else:
            # In-memory payloads remain useful for imported fixtures and previews.
            self._queries = None
            self._records = [dict(r, id=str(r.get("id") or "display:{}".format(index)))
                             for index, r in enumerate(self._data.get("records", [])) if isinstance(r, dict)]
            self._records.sort(key=lambda r: str(r.get("timestamp") or ""), reverse=True)
            self._records_by_id = {r["id"]: r for r in self._records}
            supplied = self._data.get("user_requests")
            self._user_requests = supplied if isinstance(supplied, list) else aggregate_user_requests(
                self._records, self._data.get("turns", []), self._data.get("agent_links", []), self._data.get("sources", []))
        self._dirty_pages.update(PAGE_NAMES)
        if not self._usage_loading_seen:
            self._initial_usage_done = True
        self._refresh_visible()

    def _render_overview(self) -> None:
        period = self._overview_period.value()
        granularity = "hour" if period == "today" else "week" if period == "all" else "day"
        lower, upper = period_bounds(period)
        if self._queries:
            result = self._queries.page(mode="user_request", start=lower, end=upper, page_size=4)
            recent = result["rows"]
            request_count = result.get("counts", {}).get("requests", result["total"])
            buckets = self._queries.chart_buckets(period, granularity)
        else:
            buckets = bucket_records(self._records, period, granularity)
            candidates = [row for row in self._user_requests if (stamp := parse_timestamp(row.get("timestamp"))) is not None
                          and stamp <= upper and (lower is None or stamp >= lower)]
            recent = candidates[:4]
            request_count = sum(row.get("record_kind") == "user_request" and not row.get("is_subagent") for row in candidates)
        self._overview_chart.set_buckets(buckets, granularity)
        self._token_composition.set_buckets(buckets)
        comparison = self._period_comparison(period)
        if comparison:
            summary = comparison["current"]
        elif self._queries:
            summary = self._queries.confirmed_summary(upper)
        else:
            summary = summarize_confirmed_usage(row for row in self._records
                if (stamp := parse_timestamp(row.get("timestamp"))) is not None and stamp <= upper)
        self._overview_tokens.setText("—" if summary["tokens"] is None else compact_number(summary["tokens"]))
        self._overview_cost.setText("—" if summary["usd"] is None else usd(summary["usd"]))
        self._overview_calls.setText("—" if summary["requests"] is None else format(summary["requests"], ","))
        for metric, widget in self._overview_comparisons.items():
            widget.set_comparison(comparison, metric, self._theme)
        self._overview_calls.setToolTip("%s 次用户请求" % format(request_count, ",") +
                                       ("\n按已确认数据计算" if summary["skipped"]["requests"] else ""))
        self._overview_tokens.setToolTip("输入 + 输出" + ("\n按已确认数据计算" if summary["skipped"]["tokens"] else ""))
        unpriced = int(summary.get("unpriced_tokens") or 0)
        price_note = "%s Token 未定价；当前金额仅含已定价调用" % compact_number(unpriced) if unpriced else ""
        if summary["skipped"]["usd"]:
            price_note = "\n".join(filter(None, (price_note, "按已确认数据计算")))
        self._overview_cost.setToolTip(price_note)
        self._overview_cost.setAccessibleDescription(price_note)
        self._overview_period_label.setText(next((label + "用量" for label, key in PERIODS if key == period), "用量"))
        self._recent_rows = recent
        self._recent_table.set_records(recent, request_cost_text, self._theme)
        self._recent_table.setVisible(bool(recent))
        self._recent_empty.setVisible(not recent)
        self._recent_table.setFixedHeight(43 + min(4, len(recent)) * 58 + 16)
        self._latest_record = copy.deepcopy(self._data.get("latest_request") if self._queries else
                                          next((row for row in self._user_requests if row.get("record_kind") == "user_request" and not row.get("is_subagent")), None))
    def apply_quota(self, state) -> None:
        self._quota_state = state
        status = state.get("status") if isinstance(state, dict) else getattr(state, "status", None)
        status = getattr(status, "value", status)
        if status in ("ok", "error"):
            self._initial_quota_done = True
        self._dirty_pages.update(("overview", "subscription"))
        if self._page_is_active("settings"):
            self._preview_widget()
        if self._active_page in ("overview", "subscription") and self._page_is_active(self._active_page):
            self._render_quota(state)
            if self._active_page == "subscription":
                self._render_subscription()
        if self.isVisible() and not self.isMinimized():
            self._update_startup_progress()

    def _render_quota(self, state) -> None:
        def get(obj, key, default=None):
            return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
        for key, meter in self._quota_widgets.get(self._active_page, {}).items():
            window = get(state, key)
            remaining, resets = get(window, "remaining_percent"), get(window, "resets_at")
            reset = format_reset_time(resets) if isinstance(resets, datetime) else "—"
            note = "等待额度数据"
            if isinstance(resets, datetime):
                seconds = max(0, (resets.astimezone() - datetime.now().astimezone()).total_seconds())
                note = ("约 %d 天后重置" % math.ceil(seconds / 86400) if seconds >= 86400 else
                        "约 %d 小时后重置" % math.ceil(seconds / 3600) if seconds >= 3600 else "约 %d 分钟后重置" % math.ceil(seconds / 60))
            meter.set_value(remaining, "重置 " + reset, note)
        status = getattr(get(state, "status"), "value", get(state, "status"))
        notice = str(get(state, "message") or "") if status in ("error", "stale") else ""
        success = get(state, "last_success_at")
        if notice and isinstance(success, datetime):
            notice += " · 保留 %s 的成功快照" % success.astimezone().strftime("%m/%d %H:%M")
        if self._active_page == "overview":
            self._overview_quota_note.setText(notice)
            self._overview_quota_note.setVisible(bool(notice))
        elif self._active_page == "subscription":
            self._subscription_notice.setText(notice)
            self._subscription_notice.setVisible(bool(notice))
            self._subscription_updated.setText("最近更新 " + (success.astimezone().strftime("%H:%M:%S") if isinstance(success, datetime) else "—"))
            credits = get(state, "reset_credits")
            self._reset_count.setText("%s 次可用" % credits if isinstance(credits, int) and not isinstance(credits, bool) and credits >= 0 else "— 次可用")
            details = get(state, "reset_credit_details")
            entries = sorted((row for row in (details or ()) if get(row, "status", "unknown") != "redeemed"),
                             key=lambda row: get(row, "expires_at") or datetime.max.replace(tzinfo=timezone.utc))
            self._reset_details_table.setRowCount(len(entries))
            for index, item in enumerate(entries):
                expires, known = get(item, "expires_at"), get(item, "expiry_known", False)
                if isinstance(expires, datetime):
                    seconds = (expires.astimezone() - datetime.now().astimezone()).total_seconds()
                    date = format_reset_date(expires, split_time=True)
                    remaining = "已到期" if seconds <= 0 else "%d 天后到期" % math.ceil(seconds / 86400) if seconds >= 86400 else "%d 小时后到期" % max(1, math.ceil(seconds / 3600))
                else:
                    date, remaining = ("无到期限制", "—") if known else ("截止时间未提供", "—")
                for column, text in enumerate(("1 次", date, remaining)):
                    cell = QTableWidgetItem(text)
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    cell.setToolTip("\n".join(str(value) for value in (date.replace("\n", " "), get(item, "title"), get(item, "description"), get(item, "id")) if value))
                    self._reset_details_table.setItem(index, column, cell)
            if details is None:
                note = "接口尚未提供逐次截止时间。" if credits is not None else "等待重置次数与明细。"
            elif isinstance(credits, int) and credits > len(entries):
                note = "另有 %d 次可用，接口尚未返回对应明细。" % (credits - len(entries))
            else:
                note = "暂无可用重置次数。" if credits == 0 else ""
            self._reset_details_note.setText(note)
            self._reset_details_note.setVisible(bool(note))
            self._reset_details_table.setVisible(bool(entries))
            self._reset_details_table.setFixedHeight(min(260, self._reset_details_table.horizontalHeader().sizeHint().height() + len(entries) * 48 + 2))
    def set_usage_loading(self, state: dict) -> None:
        if self._initial_loading_finished:
            return
        self._usage_loading_seen = True
        self._usage_loading_stage = str(state.get("stage") or "正在加载用量数据")
        self._initial_usage_done = not bool(state.get("loading", True))
        self._usage_error = str(state.get("error") or "")
        self._dirty_pages.add("overview")
        self._refresh_visible()

    def _update_startup_progress(self) -> None:
        if self._initial_loading_finished:
            return
        if self._initial_usage_done and self._initial_quota_done:
            self._initial_loading_finished = True
            self._startup_banner.hide()
            return
        self._startup_message.setText("正在读取会员额度" if self._initial_usage_done else self._usage_loading_stage)
        self._startup_banner.show()

    def set_progress(self, message: str) -> None:
        self._progress_message = message
        if self.isVisible() and not self.isMinimized():
            self._sidebar_status.setText(message)

    def open_page(self, name: str = "overview", period: Optional[str] = None) -> None:
        aliases = {"usage": "trends", "detail": "logs", "requests": "logs", "tokens": "overview", "prices": "pricing"}
        name = aliases.get(name, name)
        if name not in PAGE_NAMES:
            name = "overview"
        self._active_page = name
        self._sync_duration_timer()
        self._ensure_page(name)
        index = PAGE_NAMES.index(name)
        self._stack.setCurrentIndex(index)
        if hasattr(self, "_escape_inspector"):
            self._escape_inspector.setEnabled(name == "logs" and self._inspected_record is not None)
        self._page_title.setText(PAGE_LABELS[index])
        self._navigation.select_page(name)
        self._refresh_button.setVisible(name not in ("settings", "pricing"))
        self._fee_caption.setVisible(name not in ("settings", "subscription"))
        if name in ("logs", "trends", "overview") and period is not None:
            self._period_overrides[name] = period
        self._dirty_pages.add(name)
        if not self._loading:
            self.showNormal()
            self.raise_()
            self.activateWindow()
            self._refresh_visible()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_page(self._active_page)
        self._stack.setCurrentWidget(self._pages[self._active_page])
        self._refresh_visible()
        self._sync_duration_timer()
        if hasattr(self, "_escape_inspector"):
            self._escape_inspector.setEnabled(self._active_page == "logs" and self._inspected_record is not None)

    def hideEvent(self, event) -> None:
        self._duration_timer.stop()
        if hasattr(self, "_escape_inspector"):
            self._escape_inspector.setEnabled(False)
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_duration_timer"):
            self._sync_duration_timer()
            self._refresh_visible()

    def config_updated(self, config: dict) -> None:
        self._config = copy.deepcopy(config)
        self._theme = str(self._config.get("theme") or "system")
        self._navigation.set_order(self._config.get("navigation_order"))
        self._navigation.select_page(self._active_page)
        self._widget_toggle.blockSignals(True)
        self._widget_toggle.setChecked(bool(config.get("widget_visible", True)))
        self._widget_toggle.blockSignals(False)
        self._config_dirty.update(PAGE_NAMES)
        self._dirty_pages.update(PAGE_NAMES)
        self._apply_theme()
        self._refresh_visible()

    def _sync_config_controls(self, name: str) -> None:
        if name == "logs":
            self._log_table.set_source_visible(self._config.get("show_log_source") is True)
        elif name == "pricing":
            self._auto_sync.blockSignals(True)
            self._auto_sync.setChecked(bool(self._config.get("auto_sync_prices", True)))
            self._auto_sync.blockSignals(False)
        elif name == "settings":
            self._auto_update.blockSignals(True)
            self._auto_update.setChecked(bool(self._config.get("auto_update", True)))
            self._auto_update.blockSignals(False)
            self._theme_combo.blockSignals(True)
            self._theme_combo.setCurrentIndex(max(0, self._theme_combo.findData(self._theme)))
            self._theme_combo.blockSignals(False)
            self._restore_control(self._show_log_source, self._config.get("show_log_source") is True)
            self._server_estimates_enabled.setChecked(self._config.get("server_estimates_enabled", True) is True)
            self._account_since.setText("当前账号观测起点：" + (str(self._config.get("account_since")) if self._config.get("account_since") else "首次成功读取额度后记录"))
            self._refresh_source_list()
        self._config_dirty.discard(name)

    def _apply_theme(self) -> None:
        apply_theme(self, self._theme)
        colors = theme_colors(self._theme)
        for field in self.findChildren(DatePicker):
            field.set_theme(self._theme)
        self._navigation.set_theme(self._theme)
        self._search_button.setIcon(ui_icon("search", colors["muted"]))
        self._refresh_button.setIcon(ui_icon("refresh", colors["text"]))
        self._widget_toggle.setIcon(ui_icon("widget", colors["text"]))
        for widgets in self._quota_widgets.values():
            for widget in widgets.values():
                widget.set_theme(self._theme)
        if "overview" in self._pages:
            self._token_composition.set_theme(self._theme)
            self._recent_table.set_theme(self._theme)
            for widget in self._overview_comparisons.values():
                widget.set_theme(self._theme)
        if "settings" in self._pages:
            self._preview_widget()
        if "pricing" in self._pages:
            self._size_price_columns()
        if "logs" in self._pages:
            self._log_table.set_theme(self._theme)
            self._log_table.queue_columns()
        for name, attribute in (("overview", "_overview_chart"), ("trends", "_trend_chart")):
            if name in self._pages:
                getattr(self, attribute).set_theme(self._theme)
        if "trends" in self._pages:
            self._activity_chart.set_theme(self._theme)
            for widget in self._trend_comparisons.values():
                widget.set_theme(self._theme)

    def _system_theme_changed(self, *args) -> None:
        if self._theme == "system":
            self._apply_theme()

    def _toggle_widget(self, visible: bool) -> None:
        if self._loading:
            return
        self._config["widget_visible"] = visible
        self._callback("toggle_widget", visible)

    def _set_auto_sync(self, value: bool) -> None:
        if self._loading:
            return
        self._config["auto_sync_prices"] = value
        self._callback("config", copy.deepcopy(self._config))

    def _set_auto_update(self, value: bool) -> None:
        if self._loading:
            return
        self._config["auto_update"] = value
        self._callback("config", copy.deepcopy(self._config))

    def _update_trend_filter_options(self) -> None:
        available = list(dict.fromkeys(model for model in self._data.get("available_models", [])
                                       if isinstance(model, str) and model))
        current = [self._trend_model.itemData(i) for i in range(self._trend_model.count())]
        if current != [""] + available:
            previous = self._trend_model.currentData()
            self._trend_model.blockSignals(True)
            self._trend_model.clear()
            self._trend_model.addItem("全部模型", "")
            for model in available:
                self._trend_model.addItem(model, model)
            self._trend_model.setCurrentIndex(max(0, self._trend_model.findData(previous)))
            self._trend_model.blockSignals(False)
        self._trend_model.setEnabled(bool(available))
        self._trend_model.setToolTip("按本机 Codex 提供的模型筛选" if available else "暂未读取到本机 Codex 模型列表")
    def _update_log_filter_options(self) -> None:
        source_ids = {str(source) for row in self._records for source in (row.get("source_ids") or [row.get("source_id")]) if source}
        source_names = {str(source.get("id") or source.get("source_id")): str(source.get("name") or source.get("source_name") or "")
                        for source in self._data.get("sources", [])}
        model_ids = {str(r.get("model") or "未知模型") for r in self._records}
        if self._queries:
            options = self._data.get("filters") or self._queries.filters()
            model_ids = set(options.get("models", []))
            source_ids = {str(source["id"]) for source in options.get("sources", [])}
            source_names.update({str(source["id"]): str(source.get("name") or source["id"])
                                 for source in options.get("sources", [])})
        models = sorted(model_ids,
                        key=lambda model: (model != "未知模型", tuple(int(part) if part.isdecimal() else part.casefold()
                                                                   for part in re.split(r"(\d+)", model)), model),
                        reverse=True)
        pairs = [(self._log_model, "全部模型", models),
                 (self._log_source, "全部来源", sorted(source_ids))]
        for field, title, values in pairs:
            previous = field.currentData()
            field.blockSignals(True)
            field.clear()
            field.addItem(title, "")
            for value in values:
                if not value:
                    continue
                label = (source_names.get(value) or next((str(r.get("source_name") or value) for r in self._records if r.get("source_id") == value), value)) if field is self._log_source else value
                field.addItem(label, value)
            field.setCurrentIndex(max(0, field.findData(previous)))
            field.blockSignals(False)

    def _filters_changed(self, *args) -> None:
        if not self._page_is_active("logs"):
            self._dirty_pages.add("logs")
            return
        current = self._log_mode.currentData()
        if current != self._last_log_mode:
            self._tier_preferences[self._last_log_mode] = self._log_tier.currentData()
            target = self._tier_preferences.get(current, "")
            self._restore_control(self._log_tier, target)
            self._last_log_mode = current
        self._sync_tier_options()
        if hasattr(self, "_date_row"):
            self._date_row.setVisible(self._log_period.currentData() == "custom")
        if hasattr(self, "_log_table"):
            self._filter_records()

    def _filter_records(self, reset_page: bool = True) -> None:
        if not self._page_is_active("logs"):
            self._dirty_pages.add("logs")
            return
        if reset_page:
            self._page_number = 0
            self._log_preview_dismissed = False
        period = self._log_period.currentData()
        lower, upper = period_bounds(period)
        if period == "custom":
            zone = datetime.now().astimezone().tzinfo
            lower = datetime.combine(self._date_start.date().toPython(), time.min, tzinfo=zone)
            upper = datetime.combine(self._date_end.date().toPython(), time.max, tzinfo=zone)
        source, model, tier = self._log_source.currentData(), self._log_model.currentData(), self._log_tier.currentData()
        if self._queries:
            self._query_filters = dict(mode=self._log_mode.currentData(), start=lower, end=upper,
                                       source=source or "", model=model or "", tier=tier or "", search=self._log_search.text().strip())
            self._render_log_page()
            return
        rows = []
        grouped = self._log_mode.currentData() == "user_request"
        for row in (self._user_requests if grouped else self._records):
            stamp = parse_timestamp(row.get("timestamp"))
            if stamp is None or stamp > upper or (lower and stamp < lower):
                continue
            search = self._log_search.text().strip().casefold()
            if search and search not in " ".join(str(row.get(key) or "") for key in ("prompt_preview", "session_title", "session_id", "turn_id", "id", "model", "models")).casefold():
                continue
            if grouped:
                if tier == "mixed" and row.get("service_tier") != "mixed":
                    continue
                members = [self._records_by_id[ident] for ident in row.get("member_ids", []) if ident in self._records_by_id]
                if members:
                    if not any(matches_call(member, source, model, "" if tier == "mixed" else tier) for member in members):
                        continue
                elif model or tier or source and source not in row.get("source_ids", []):
                    continue
            elif tier == "mixed" or not matches_call(row, source, model, tier):
                continue
            rows.append(row)
        self._filtered_records = rows
        self._render_log_page()

    def _render_log_page(self) -> None:
        grouped = self._log_mode.currentData() == "user_request"
        if self._log_table.set_mode(grouped):
            self._rendered_log_rows = None
        if self._queries:
            result = self._queries.page(**self._query_filters, page=self._page_number, page_size=self._page_size)
            self._query_page = result
            count, page_count = result["total"], result["pages"]
            self._page_number = result["page"]
            rows = self._filtered_records = result["rows"]
        else:
            count = len(self._filtered_records)
            page_count = max(1, math.ceil(count / self._page_size))
            self._page_number = max(0, min(self._page_number, page_count - 1))
            rows = self._filtered_records[self._page_number * self._page_size:(self._page_number + 1) * self._page_size]
        if getattr(self, "_rendered_log_rows", None) == rows:
            self._update_log_navigation(count, page_count)
            self._select_default_log_preview()
            return
        previous_rows = getattr(self, "_rendered_log_rows", None) or []
        selected = self._log_table.currentRow()
        selected_id = previous_rows[selected].get("id") if 0 <= selected < len(previous_rows) else self.__dict__.pop("_saved_log_selection", None)
        scroll = self.__dict__.pop("_saved_log_scroll", self._log_table.verticalScrollBar().value())
        self._rendered_log_rows = rows if self._queries else copy.deepcopy(rows)
        self._log_table.set_records(rows, request_cost_text, self._theme)
        if selected_id:
            for index, row in enumerate(rows):
                if row.get("id") == selected_id:
                    self._log_table.selectRow(index)
                    break
        self._log_table.verticalScrollBar().setValue(scroll)
        self._update_log_navigation(count, page_count)
        if self._inspected_record:
            ident = self._inspector_origin or self._inspected_record.get("id")
            latest = next((row for row in rows if row.get("id") == ident), None)
            if latest is not None and latest != self._inspected_record:
                self._show_inspector(latest, focus=False)
            elif latest is None:
                self._close_inspector(restore_focus=False)
        self._select_default_log_preview()

    def _select_default_log_preview(self) -> None:
        """Open the first available page-one row without overriding user intent."""
        rows = self._rendered_log_rows or []
        if self._page_number != 0 or not rows or self._inspected_record or self._log_preview_dismissed:
            return
        selected = self._log_table.currentRow()
        # A restored selection takes precedence over the initial first-row default.
        selected = selected if 0 <= selected < len(rows) else 0
        self._log_table.selectRow(selected)
        self._inspect_log_row(selected)

    def _update_log_navigation(self, count: int, page_count: int) -> None:
        if self._log_mode.currentData() == "user_request":
            if self._queries:
                counts = self._query_page["counts"]
                unassigned, children = counts["unassigned"], counts["subagents"]
            else:
                unassigned = sum(row.get("record_kind") == "unassigned" for row in self._filtered_records)
                children = sum(bool(row.get("is_subagent")) for row in self._filtered_records if row.get("record_kind") != "unassigned")
            pieces = ["%s 次用户请求" % format(count - unassigned - children, ",")]
            if children:
                pieces.append("%s 次独立子代理请求" % format(children, ","))
            if unassigned:
                pieces.append("%s 条未归属调用" % format(unassigned, ","))
            label = " · ".join(pieces)
        else:
            label = "%s 条模型调用记录" % format(count, ",")
        self._log_count.setText(label + " · 每页 100 条")
        self._page_indicator.setText("%s / %s" % (self._page_number + 1, page_count))
        self._previous.setEnabled(self._page_number > 0)
        self._next.setEnabled(self._page_number + 1 < page_count)
        self._sync_duration_timer()

    def _change_page(self, delta: int) -> None:
        self._page_number += delta
        self._log_preview_dismissed = False
        self._render_log_page()

    def _dialog(self, dialog: QDialog) -> None:
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda *_: self._dialogs.remove(dialog) if dialog in self._dialogs else None)
        dialog.open()

    def _show_request_row(self, row: int, column: int = 0) -> None:
        index = row if self._queries else self._page_number * self._page_size + row
        if 0 <= index < len(self._filtered_records):
            record = self._filtered_records[index]
            if record.get("record_kind") == "user_request":
                members = [self._records_by_id[ident] for ident in record.get("member_ids", []) if ident in self._records_by_id]
                self._dialog(UserRequestDetails(record, members, self._theme, self, queries=self._queries))
            else:
                if record.get("record_kind") == "unassigned":
                    member_id = next(iter(record.get("member_ids", [])), "")
                    record = (self._queries.record(member_id) if self._queries else self._records_by_id.get(member_id)) or record
                elif self._queries:
                    record = self._queries.record(record["id"]) or record
                self._dialog(RequestDetails(record, self._theme, self))

    def _update_trends(self, *args) -> None:
        if not self._page_is_active("trends"):
            self._dirty_pages.add("trends")
            return
        if hasattr(self, "_trend_chart"):
            self._trend_metrics_box.setVisible(self._trend_period.currentData() in ("today", "week", "month"))
            model = self._trend_model.currentData()
            rows = [row for row in self._records if not model or row.get("model") == model]
            self._update_activity(model, rows)
            start = end = None
            if self._trend_period.currentData() == "custom":
                zone = datetime.now().astimezone().tzinfo
                start = datetime.combine(self._trend_start.date().toPython(), time.min, tzinfo=zone)
                end = datetime.combine(self._trend_end.date().toPython(), time.max, tzinfo=zone)
                if start > end:
                    self._trend_chart.set_records([], "all")
                    self._trend_note.setText("起始日期应早于或等于结束日期。")
                    self._trend_note.show()
                    return
            if self._queries:
                granularity = self._granularity.currentData()
                buckets = self._queries.chart_buckets(self._trend_period.currentData(), granularity,
                                                      start=start, end=end, model=model or "")
                self._trend_chart.set_buckets(buckets, granularity)
            else:
                self._trend_chart.set_records(rows, self._trend_period.currentData(), self._granularity.currentData(), start=start, end=end)
            comparison = self._period_comparison(self._trend_period.currentData(), model or "")
            for metric, widget in self._trend_comparisons.items():
                widget.set_comparison(comparison, metric, self._theme)
                current = (comparison or {}).get("current", {})
                value = current.get(metric)
                self._trend_metric_values[metric].setText("—" if value is None else
                    usd(value) if metric == "usd" else compact_number(value) if metric == "tokens" else format(value, ","))
            if hasattr(self, "_trend_note"):
                self._trend_note.clear()
                self._trend_note.hide()

    def _period_comparison(self, period, model=""):
        if period not in ("today", "week", "month"):
            return None
        now = datetime.now().astimezone()
        key = (period, model, now.replace(second=0, microsecond=0))
        if key not in self._comparison_cache:
            if self._queries and hasattr(self._queries, "period_comparison"):
                result = self._queries.period_comparison(period, now=now, model=model)
            else:
                result = compare_usage((row for row in self._records if not model or row.get("model") == model), period, now)
            if len(self._comparison_cache) >= 16:
                self._comparison_cache.clear()
            self._comparison_cache[key] = result
        return self._comparison_cache[key]

    def _update_activity(self, model, rows):
        start, now = activity_bounds()
        # A publication can reveal a future-dated record without rebuilding the
        # index. Cache within that publication, rather than only its DB version.
        key = (self._activity_revision, model, now.date())
        if self._activity_key == key:
            return
        if self._queries:
            buckets = self._queries.chart_buckets("all", "day", start=start, end=now, model=model or "")
        else:
            buckets = bucket_records(rows, "all", "day", start=start, end=now)
        self._activity_chart.set_buckets(buckets, now)
        self._activity_summary.setText(self._activity_chart.summary_text())
        self._activity_range.setText(self._activity_chart.range_text())
        self._activity_key = key

    def _activity_day_open(self, bucket):
        model = self._trend_model.currentData() or ""
        self._chart_bucket_open(bucket)
        self._log_search.clear()
        for field in (self._log_source, self._log_tier):
            self._restore_control(field, "")
        self._restore_control(self._log_model, model)
        self._restore_control(self._log_mode, "model_call")
        self._last_log_mode = "model_call"
        self._filters_changed()

    def _trend_period_changed(self, *args) -> None:
        period = self._trend_period.currentData()
        if hasattr(self, "_trend_date_row"):
            self._trend_date_row.setVisible(period == "custom")
        default = {"today": "hour", "week": "day", "month": "day", "all": "week"}.get(period)
        if default:
            self._granularity.blockSignals(True)
            self._granularity.setCurrentIndex(self._granularity.findData(default))
            self._granularity.blockSignals(False)
        self._update_trends()

    def _update_prices(self, *args) -> None:
        if not self._page_is_active("pricing"):
            self._dirty_pages.add("pricing")
            return
        if not hasattr(self, "_price_table"):
            return
        search = self._price_search.text().strip().lower()
        available = list(dict.fromkeys(self._data.get("available_models", [])))
        allowed = set(available)
        standard = self._data.get("standard_prices")
        if standard is None:
            standard = [dict(p, **p.get("base_rates", {})) for p in self._data.get("prices", [])
                        if isinstance(p, dict) and p.get("service_tier", "default") in ("default", "standard")
                        and not p.get("threshold")]
        self._standard_prices_by_model = {p["model"]: p for p in standard if isinstance(p, dict) and p.get("model") in allowed}
        converted = {}
        for price in self._data.get("prices", []):
            if isinstance(price, dict) and price.get("model") in allowed and price.get("service_tier", "default") in ("default", "standard", "priority", "fast"):
                converted.setdefault(price["model"], []).append(price)
        self._visible_prices = []
        for model in available:
            if search not in model.lower():
                continue
            variants = sorted(converted.get(model, []), key=lambda p:
                              (p.get("service_tier", "default") in ("priority", "fast"), int(p.get("threshold") or 0)))
            self._visible_prices.extend(dict(price) for price in variants or [{"model": model, "service_tier": "default"}])
        self._price_table.blockSignals(True)
        self._price_table.setRowCount(len(self._visible_prices))
        selected = None
        for index, price in enumerate(self._visible_prices):
            context = ("\n>%s" % compact_number(price["threshold"]) if price.get("threshold") else
                       ("\n≤%s" % compact_number(price["long_context_threshold"]) if price.get("long_context_threshold") else ""))
            tier = {"default": "Standard", "standard": "Standard", "priority": "Fast", "fast": "Fast"}.get(price.get("service_tier") or "default", "Standard")
            condition = tier + context
            values = [price.get("model") or "—", condition,
                      *[price_rate_text(price.get(k)) for k in ("input", "cache_read", "cache_write", "output")]]
            multiplier = price.get("multipliers", {})
            if tier == "Standard" and not price.get("threshold"):
                rule = "OpenAI API Standard 标准价；使用官方输入、缓存读取、缓存创建与输出单价，未公布的单价保留未定价。"
            elif multiplier:
                rule = "标准 API 基础价 × Codex 倍率\n输入 / 缓存读取 ×%g，输出 ×%g\n缓存创建按普通输入单价，无写入附加费" % (multiplier["input"], multiplier["output"])
                if price.get("model") == "gpt-6-astra":
                    rule += "\nAstra 长上下文不额外加价"
            else:
                rule = "尚无完整的换算规则"
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(value + "\n" + rule + ("\n使用手工基础价" if price.get("locked") else ""))
                if tier == "Standard" and not price.get("threshold"):
                    font = QFont(self._price_table.font())
                    font.setBold(True)
                    item.setFont(font)
                self._price_table.setItem(index, column, item)
            if self._price_row_key(price) == self._selected_price_key:
                selected = index
        if selected is None:
            self._selected_price_model = self._selected_price_key = None
            self._price_table.clearSelection()
            self._price_table.setCurrentCell(-1, -1)
        else:
            self._price_table.selectRow(selected)
        self._price_table.blockSignals(False)
        self._edit_base_price.setEnabled(selected is not None)
        self._reset_base_price.setEnabled(selected is not None)
        self._price_empty.setVisible(not self._visible_prices)
        self._size_price_columns()
        status = self._data.get("pricing_status") or {}
        if isinstance(status, dict):
            label = {"bundled": "基础价已就绪", "synced": "基础价已同步", "fallback": "基础价已同步", "offline": "同步失败，沿用已有基础价", "conflict": "部分基础价更新待核验"}.get(status.get("status"), "基础价已就绪")
            stamp = parse_timestamp(status.get("updated_at") or status.get("last_sync_at"))
            catalog_status = self._data.get("model_catalog_status") or {}
            note = "仅显示本机 Codex 提供的 %d 个模型" % len(available)
            if not available:
                note = "暂未读取到本机 Codex 模型列表"
            elif catalog_status.get("status") == "stale":
                note += "，当前使用上次读取的列表"
            self._price_status.setText(note + "\n" + label + (" · " + stamp.strftime("%m/%d %H:%M") if stamp else ""))

    @staticmethod
    def _price_row_key(price):
        return (price.get("model"), price.get("service_tier", "default"), price.get("threshold", 0))

    def _size_price_columns(self) -> None:
        if not hasattr(self, "_price_table"):
            return
        header = self._price_table.horizontalHeader()
        metrics = header.fontMetrics()
        minimum = max(metrics.horizontalAdvance(self._price_table.horizontalHeaderItem(column).text()) + 30
                      for column in range(self._price_table.columnCount()))
        header.setMinimumSectionSize(max(104, minimum))

    def _price_selection_changed(self):
        row = self._price_table.currentRow()
        values = getattr(self, "_visible_prices", [])
        selected = values[row] if 0 <= row < len(values) else None
        self._selected_price_model = selected["model"] if selected else None
        self._selected_price_key = self._price_row_key(selected) if selected else None
        self._edit_base_price.setEnabled(selected is not None)
        self._reset_base_price.setEnabled(selected is not None)

    def _edit_price(self, price: dict) -> None:
        dialog = PriceEditor(price, self)
        apply_theme(dialog, self._theme)
        dialog.accepted.connect(lambda: self._callback("price_override", dialog.model.text().strip(), dialog.rates()))
        self._dialog(dialog)

    def _edit_selected_price(self) -> None:
        if self._selected_price_model:
            self._edit_price(self._standard_prices_by_model.get(self._selected_price_model, {"model": self._selected_price_model}))

    def _reset_selected_price(self) -> None:
        if self._selected_price_model:
            self._callback("price_override", self._selected_price_model, None)

    def _update_estimate(self) -> None:
        selected = select_estimates(self._data)
        value = selected["primary"]
        amount = estimate_amount(value)
        self._estimate_value.setText(amount if amount != "—" else "待采样")
        detail = estimate_detail(value)
        self._estimate_value.setToolTip(detail)
        delta = value.get("delta_percent")
        label = method_label(value) if value else "尚无有效样本"
        if selected["cached"] and str(value.get("method", "")).startswith("server_"):
            label = "缓存 · " + label
        self._estimate_note.setText(label + (" · 已采样 %g 个百分点" % float(delta) if delta is not None else ""))
        self._estimate_note.setToolTip(detail)
        message = selected["message"]
        if not str(value.get("method", "")).startswith("server_") and value.get("estimated_remaining_usd") is not None:
            message = "剩余额度参考 " + usd(value["estimated_remaining_usd"]) + " · " + message
        self._estimate_period.setText(message)
        reference = selected["reference"]
        self._estimate_reference.setText("本地观测参考：" + estimate_amount(reference) if reference and value is not reference else "")
        self._estimate_reference.setToolTip(estimate_detail(reference or {}))

    def _fill_estimate_history(self, view):
        estimates = estimate_history(self._data)
        view.setRowCount(len(estimates))
        for index, value in enumerate(estimates):
            reset = value.get("reset_at")
            try:
                reset_text = format_reset_date(datetime.fromtimestamp(float(reset)), split_time=True) if reset is not None else "—"
            except (ValueError, TypeError, OverflowError, OSError):
                reset_text = "—"
            interval, detail = estimate_interval(value)
            status, _ = estimate_status(value)
            pool = {"codex": "Codex", "codex_bengalfox": "Spark"}.get(value.get("limit_id"), "未识别")
            columns = (str(value.get("plan_type") or "—").upper(), reset_text, interval, estimate_amount(value),
                       status, "服务端采样" if str(value.get("method", "")).startswith("server_") and value.get("status") != "ready" else
                       "服务端 · 日界对齐" if value.get("method") == "server_aligned" else
                       "服务端 · 跨多日" if value.get("method") == "server_range" else "本地观测参考", pool)
            for column, text in enumerate(columns):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(detail if column == 2 else estimate_detail(value))
                view.setItem(index, column, item)

    def _show_estimates(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("周额度估值记录")
        dialog.resize(1150, 460)
        apply_theme(dialog, self._theme)
        layout = QVBoxLayout(dialog)
        view = table(ESTIMATE_HEADERS)
        self._estimate_history_table = view
        self._fill_estimate_history(view)
        header = view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(130)
        header.setStretchLastSection(False)
        layout.addWidget(view)
        layout.addWidget(plain_label("服务端为已入账数据估值，本地算法单列参考；非订阅实际扣款。", muted=True, wrap=True))
        self._dialog(dialog)

    def _save_settings(self) -> None:
        values = {}
        for key, widget in self._setting_widgets.items():
            values[key] = widget.currentData() if isinstance(widget, QComboBox) else widget.isChecked() if isinstance(widget, QCheckBox) else widget.value() if isinstance(widget, QSpinBox) else widget.text().strip()
        if not QColor(str(values["border_color"])).isValid():
            self._settings_message.setText("边框颜色无效，请使用 #RRGGBB。")
            return
        self._settings = replace(self._settings, **values).normalized()
        self._config["theme"] = self._theme_combo.currentData()
        self._config["show_log_source"] = self._show_log_source.isChecked()
        self._config["server_estimates_enabled"] = self._server_estimates_enabled.isChecked()
        self._theme = self._config["theme"]
        self._callback("settings", self._settings)
        self._callback("config", copy.deepcopy(self._config))
        if "logs" in self._pages:
            self._log_table.set_source_visible(self._config["show_log_source"])
        self._apply_theme()
        self._settings_message.setText("设置已保存")

    def _refresh_source_list(self) -> None:
        self._source_list.clear()
        self._source_entries = []
        roots = self._config.get("codex_roots") or []
        for index, root in enumerate(roots):
            self._source_list.addItem("本机  ·  " + str(root))
            self._source_entries.append(("local", index, root))
        for kind, key in (("ssh", "ssh_sources"),):
            for index, source in enumerate(self._config.get(key) or []):
                self._source_list.addItem("%s  ·  %s  ·  %s%s" % (kind.upper(), source.get("name") or source.get("host") or "未命名", source.get("host") or "", "（停用）" if not source.get("enabled", True) else ""))
                self._source_entries.append((kind, index, source))
        if not self._source_entries:
            self._source_list.addItem("尚未配置来源，添加一个本机目录或远程主机。")

    def _edit_source(self, kind: Optional[str] = None) -> None:
        index = None
        value = {}
        if kind is None:
            row = self._source_list.currentRow()
            if not 0 <= row < len(self._source_entries):
                return
            kind, index, original = self._source_entries[row]
            value = {"root": original} if kind == "local" else dict(original)
        dialog = SourceEditor(kind, value, self)
        apply_theme(dialog, self._theme)
        dialog.accepted.connect(lambda: self._save_source(kind, index, dialog.result_value()))
        self._dialog(dialog)

    def _save_source(self, kind: str, index: Optional[int], value: dict) -> None:
        key = {"local": "codex_roots", "ssh": "ssh_sources"}[kind]
        sources = list(self._config.get(key) or [])
        entry = value["root"] if kind == "local" else value
        if index is None:
            if kind == "local" and entry in sources:
                return
            sources.append(entry)
        else:
            sources[index] = entry
        self._config[key] = sources
        self._callback("config", copy.deepcopy(self._config))
        self._refresh_source_list()

    def _remove_source(self) -> None:
        row = self._source_list.currentRow()
        if not 0 <= row < len(self._source_entries):
            return
        kind, index, value = self._source_entries[row]
        key = {"local": "codex_roots", "ssh": "ssh_sources"}[kind]
        sources = list(self._config.get(key) or [])
        sources.pop(index)
        self._config[key] = sources
        self._callback("config", copy.deepcopy(self._config))
        self._refresh_source_list()

    def _update_sources_status(self) -> None:
        labels = []
        for source in self._data.get("sources") or []:
            status = {"ok": "已同步", "ready": "就绪", "indexed": "已索引", "error": "连接失败", "scanning": "扫描中"}.get(source.get("status"), source.get("status") or "就绪")
            labels.append("%s：%s" % (source.get("name") or source.get("id") or "数据源", source.get("message") or source.get("error") or status))
        self._sources_status.setText("\n".join(labels))

    def _assign_history(self) -> None:
        records = self._records
        if self._queries:
            records = [dict(source_id=option["id"], source_name=option["name"], timestamp=option["start"])
                       for option in self._queries.history_source_options()]
        dialog = HistoryAssignmentEditor(records, self._data.get("sources") or [], self)
        apply_theme(dialog, self._theme)
        dialog.accepted.connect(lambda: self._callback("assign_history", dialog.result_value()))
        self._dialog(dialog)

    def closeEvent(self, event) -> None:
        self._callback("main_hidden", bytes(self.saveGeometry()).hex())
        self._callback("main_closed", self)
        event.accept()

    def _build_subscription(self) -> QWidget:
        page, contents = self._page(scroll=True)
        page.setObjectName("subscriptionScroll")
        heading = QHBoxLayout()
        title = plain_label("当前额度")
        title.setProperty("subheading", True)
        heading.addWidget(title)
        heading.addStretch()
        self._subscription_updated = plain_label("最近更新 —", muted=True)
        heading.addWidget(self._subscription_updated)
        contents.addLayout(heading)
        self._subscription_notice = plain_label("", muted=True, wrap=True)
        self._subscription_notice.hide()
        contents.addWidget(self._subscription_notice)
        columns = QHBoxLayout()
        columns.setSpacing(18)
        profile, profile_layout = card()
        profile.setMinimumWidth(185)
        profile.setMaximumWidth(235)
        self._subscription_plan = plain_label("—", wrap=True)
        self._subscription_plan.setProperty("heading", True)
        profile_layout.addWidget(self._subscription_plan)
        profile_layout.addWidget(plain_label("个人订阅计划", muted=True))
        profile_layout.addSpacing(14)
        self._subscription_price = plain_label("未填写")
        self._subscription_price.setProperty("metric", True)
        profile_layout.addWidget(self._subscription_price)
        profile_layout.addWidget(plain_label("订阅价格", muted=True))
        profile_layout.addSpacing(15)
        profile_layout.addWidget(plain_label("续费日期", muted=True))
        self._subscription_renewal = plain_label("未填写")
        profile_layout.addWidget(self._subscription_renewal)
        profile_layout.addStretch()
        edit = QPushButton("编辑资料")
        edit.setProperty("quiet", True)
        edit.clicked.connect(self._edit_subscription_profile)
        profile_layout.addWidget(edit)
        columns.addWidget(profile, 1)
        right = QVBoxLayout()
        right.setSpacing(18)
        quotas = QHBoxLayout()
        quotas.setSpacing(16)
        meters = {}
        for key, title in (("five_hour", "5 小时额度"), ("week", "本周额度")):
            meter = QuotaMeter(title, gauge=True)
            meters[key] = meter
            quotas.addWidget(meter, 1)
        self._quota_widgets["subscription"] = meters
        right.addLayout(quotas)
        credits, credit_layout = card()
        credit_layout.setContentsMargins(22, 20, 4, 20)
        credit_heading = QHBoxLayout()
        credit_heading.setContentsMargins(0, 0, 18, 0)
        credit_heading.addWidget(plain_label("主动重置"), 1)
        self._reset_count = plain_label("— 次可用", muted=True)
        credit_heading.addWidget(self._reset_count)
        credit_layout.addLayout(credit_heading)
        self._reset_details_table = table(["次数", "截止时间", "剩余时间"])
        self._reset_details_table.setObjectName("resetCreditTable")
        self._reset_details_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._reset_details_table.verticalHeader().setDefaultSectionSize(48)
        self._reset_details_table.setMinimumHeight(118)
        self._reset_details_table.setMaximumHeight(260)
        credit_layout.addWidget(self._reset_details_table)
        self._reset_details_note = plain_label("尚未读取逐次明细", muted=True, wrap=True)
        credit_layout.addWidget(self._reset_details_note)
        credit_layout.addStretch()
        right.addWidget(credits)
        columns.addLayout(right, 3)
        contents.addLayout(columns)
        self._subscription_history_section, history_layout = card()
        self._subscription_history_section.setObjectName("subscriptionHistorySection")
        history_layout.setContentsMargins(22, 20, 4, 20)
        title = plain_label("周期记录")
        title.setProperty("subheading", True)
        history_layout.addWidget(title)
        summary = QHBoxLayout()
        values = QVBoxLayout()
        values.addWidget(plain_label("整周额度估值", muted=True))
        self._estimate_value = plain_label("待采样", wrap=True)
        self._estimate_value.setProperty("metric", True)
        self._estimate_note = plain_label("", muted=True, wrap=True)
        self._estimate_period = plain_label("", muted=True, wrap=True)
        self._estimate_reference = plain_label("", muted=True, wrap=True)
        values.addWidget(self._estimate_value)
        values.addWidget(self._estimate_note)
        values.addWidget(self._estimate_period)
        values.addWidget(self._estimate_reference)
        summary.addLayout(values, 1)
        history_layout.addLayout(summary)
        self._subscription_history = table(ESTIMATE_HEADERS)
        self._subscription_history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._subscription_history.setMinimumHeight(260)
        self._subscription_history.setMaximumHeight(420)
        history_layout.addWidget(self._subscription_history)
        history_layout.addWidget(plain_label("日数据按 UTC（北京时间 08:00）分日，结束满 24 小时后纳入，后续补账自动修正。金额为额度等价估算；悬停可查看范围与采样依据。", muted=True, wrap=True))
        contents.addWidget(self._subscription_history_section)
        contents.addStretch()
        return page

    def _overview_period_changed(self, period):
        self._dirty_pages.add("overview")
        self._refresh_visible()

    def _sync_tier_options(self):
        grouped = self._log_mode.currentData() == "user_request"
        index = self._log_tier.findData("mixed")
        item = self._log_tier.model().item(index) if index >= 0 else None
        if item is not None:
            item.setEnabled(grouped)
        if not grouped and self._log_tier.currentData() == "mixed":
            self._restore_control(self._log_tier, "")

    def _chart_bucket_open(self, bucket):
        stamp = bucket.get("timestamp")
        if not isinstance(stamp, datetime):
            return
        self.open_page("logs")
        self._loading = True
        self._log_period.setCurrentIndex(self._log_period.findData("custom"))
        date = QDate(stamp.year, stamp.month, stamp.day)
        self._date_start.setDate(date)
        self._date_end.setDate(date)
        self._date_row.show()
        self._loading = False
        self._filter_records()

    def _open_recent_request(self, row, column=0):
        if not 0 <= row < len(self._recent_rows):
            return
        record = self._recent_rows[row]
        period = self._overview_period.value()
        self.open_page("logs", period)
        self._loading = True
        self._log_search.clear()
        for field in (self._log_source, self._log_model, self._log_tier):
            field.setCurrentIndex(0)
        self._log_mode.setCurrentIndex(self._log_mode.findData("user_request"))
        self._loading = False
        self._filter_records()
        for index, value in enumerate(self._filtered_records):
            if value.get("id") == record.get("id"):
                self._log_table.selectRow(index)
                break
        self._inspector_origin = record.get("id")
        self._show_inspector(record)

    def _profile_plan_text(self):
        profile = normalize_subscription_profile(self._config.get("subscription_profile"))
        state = self._quota_state
        observed = state.get("plan_type") if isinstance(state, dict) else getattr(state, "plan_type", None)
        value = profile["plan"] or observed or "待连接"
        aliases = {"pro20x": "Pro 20×", "pro": "Pro", "plus": "Plus", "free": "Free", "team": "Team", "enterprise": "Enterprise"}
        return aliases.get(str(value).lower().replace(" ", "").replace("×", "x"), str(value))

    def _render_subscription(self):
        profile = normalize_subscription_profile(self._config.get("subscription_profile"))
        self._subscription_plan.setText(self._profile_plan_text())
        self._subscription_price.setText(usd(profile["price_usd"]) if profile["price_usd"] is not None else "未填写")
        self._subscription_renewal.setText(profile["renewal_date"] or "未填写")
        self._update_estimate()
        view = self._subscription_history
        view.horizontalHeader().setMinimumSectionSize(max(
            view.fontMetrics().horizontalAdvance(view.horizontalHeaderItem(column).text()) + 30
            for column in range(view.columnCount())))
        self._fill_estimate_history(view)

    def _edit_subscription_profile(self):
        profile = normalize_subscription_profile(self._config.get("subscription_profile"))
        dialog = QDialog(self)
        dialog.setWindowTitle("订阅资料")
        dialog.resize(410, 270)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        plan = QLineEdit(profile["plan"] or self._profile_plan_text())
        plan.setMaxLength(64)
        displayed_price = "" if profile["price_usd"] is None else price_rate_text(profile["price_usd"])
        price = QLineEdit(displayed_price)
        price.setPlaceholderText("未填写")
        renewal = QLineEdit(profile["renewal_date"])
        renewal.setPlaceholderText("YYYY-MM-DD，可留空")
        for label, field in (("个人计划", plan), ("订阅价格（美元）", price), ("续费日期", renewal)):
            form.addRow(label, field)
        layout.addLayout(form)
        note = plain_label("只记录个人资料，不会变更实际订阅或产生扣款。", muted=True, wrap=True)
        layout.addWidget(note)
        buttons = dialog_buttons(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        def save():
            amount = profile["price_usd"] if price.text().strip() == displayed_price else price.text().strip() or None
            raw = dict(plan=plan.text().strip(), price_usd=amount, renewal_date=renewal.text().strip())
            value = normalize_subscription_profile(raw)
            if raw["price_usd"] is not None and value["price_usd"] is None:
                note.setText("请输入有效的美元金额。")
                price.setFocus()
                return
            if raw["renewal_date"] and not value["renewal_date"]:
                note.setText("续费日期请使用 YYYY-MM-DD。")
                renewal.setFocus()
                return
            self._config["subscription_profile"] = value
            self._callback("config", copy.deepcopy(self._config))
            self._dirty_pages.add("subscription")
            self._refresh_visible()
            dialog.accept()
        buttons.accepted.connect(save)
        apply_theme(dialog, self._theme)
        self._dialog(dialog)

    def _preview_theme(self, *args):
        if self._loading:
            return
        self._theme = self._theme_combo.currentData()
        self._apply_theme()

    def _create_inspector(self):
        frame = QFrame()
        frame.setObjectName("requestInspector")
        frame.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        frame.setAccessibleName("请求详情，点击左侧请求查看，按 Escape 清空选择")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(17, 14, 4, 14)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 13, 0)
        self._inspector_heading = LineLimitedText("请求详情", max_lines=1, fit_content=True)
        self._inspector_heading.setObjectName("inspectorSessionTitle")
        header.addWidget(self._inspector_heading, 1)
        layout.addLayout(header)
        self._inspector_scroll = QScrollArea()
        self._inspector_scroll.setWidgetResizable(True)
        self._inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._inspector_stack = QStackedWidget()
        self._inspector_empty = plain_label("点击左侧请求\n在这里查看详情", muted=True, wrap=True)
        self._inspector_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._inspector_stack.addWidget(self._inspector_empty)
        self._inspector_stack.addWidget(self._inspector_scroll)
        layout.addWidget(self._inspector_stack, 1)
        self._escape_inspector = QShortcut(QKeySequence("Escape"), frame)
        self._escape_inspector.setContext(Qt.ShortcutContext.WindowShortcut)
        self._escape_inspector.setEnabled(False)
        self._escape_inspector.activated.connect(self._close_inspector)
        return frame

    def _inspect_log_row(self, row, column=0):
        rows = getattr(self, "_rendered_log_rows", None) or []
        if 0 <= row < len(rows):
            self._inspector_origin = rows[row].get("id")
            self._show_inspector(rows[row], focus=False)

    def _show_inspector(self, record, *, focus=True):
        self._inspector_origin = record.get("id")
        if record.get("record_kind") == "unassigned":
            member = next(iter(record.get("member_ids", [])), None)
            if member:
                record = (self._queries.record(member) if self._queries else self._records_by_id.get(member)) or record
        same_record = (self._inspected_record or {}).get("id") == record.get("id")
        old_scroll = self._inspector_scroll.verticalScrollBar().value() if same_record else None
        self._inspected_record = copy.deepcopy(record)
        grouped = record.get("record_kind") == "user_request"
        session_title = str(record.get("session_title") or "未记录会话标题")
        self._inspector_heading.set_text(session_title)
        self._inspector_heading.setToolTip(session_title)
        previous = self._inspector_scroll.takeWidget()
        if previous:
            previous.hide()
            previous.deleteLater()
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 16, 5)
        layout.setSpacing(20)
        def section(name, spacing=8):
            widget = QWidget()
            widget.setObjectName(name)
            section_layout = QVBoxLayout(widget)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(spacing)
            layout.addWidget(widget)
            return section_layout

        request_header = section("inspectorRequestHeader")
        request_header.addWidget(RequestPreviewText(record.get("prompt_preview")))

        summary = section("inspectorUsageSummary", spacing=12)
        stamp = parse_timestamp(record.get("timestamp"))
        summary.addWidget(plain_label(("发起时间 " if grouped else "计量时间 ") + (stamp.strftime("%Y/%m/%d %H:%M:%S") if stamp else "—"), muted=True, wrap=True))
        cost_lines = request_cost_text(record).splitlines()
        value = plain_label(cost_lines[0], wrap=True)
        value.setProperty("metric", True)
        summary.addWidget(value)
        caption = "API 等价 · " + ("整轮累计" if grouped else "本次调用")
        if len(cost_lines) > 1:
            caption += " · " + " · ".join(cost_lines[1:])
        summary.addWidget(plain_label(caption, muted=True, wrap=True))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        fields = [("模型", record.get("model") or "未知模型"), ("档位", tier_label(record)),
                  ("输入（含缓存）", format(int(record.get("input_tokens") or 0), ",")),
                  ("其中缓存读取", format(int(record.get("cached_input_tokens") or 0), ",")),
                  ("输出", format(int(record.get("output_tokens") or 0), ",")),
                  ("Total Token", format(int(record.get("total_tokens") or 0), ",")),
                  ("整轮耗时" if grouped else "调用耗时", duration_text(record)),
                  ("来源", record.get("source_name") or record.get("source_id") or "—")]
        self._inspector_duration = None
        for label, text in fields:
            field = plain_label(text, wrap=True)
            field.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            field.setMinimumWidth(0)
            form.addRow(plain_label(label, muted=True), field)
            if "耗时" in label:
                field.setToolTip(duration_tooltip(record))
                self._inspector_duration = field
        summary.addLayout(form)

        reply = section("inspectorReplyPreview")
        reply.addWidget(plain_label("回复预览", muted=True))
        output = plain_label(str(record.get("output_preview") or "尚无可见回复"), wrap=True)
        output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        output.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        reply.addWidget(output)

        identifiers = section("inspectorIdentifiers")
        for label, value in (("Turn ID", record.get("turn_id") or record.get("request_turn_id")),
                             ("Session ID", record.get("session_id")),
                             ("Response ID", record.get("response_id") if not grouped else None),
                             ("调用 ID", record.get("id") if not grouped else None)):
            if not value and label not in ("Turn ID", "Session ID"):
                continue
            identifiers.addWidget(plain_label(label, muted=True))
            field = QLineEdit(str(value or "未记录"))
            field.setReadOnly(True)
            field.setCursorPosition(0)
            field.setAccessibleName(label)
            if label == "Session ID":
                session_row = QHBoxLayout()
                session_row.addWidget(field, 1)
                copy_button = QPushButton("复制")
                copy_button.setProperty("quiet", True)
                copy_button.clicked.connect(lambda checked=False, value=field.text(): QApplication.clipboard().setText(value))
                session_row.addWidget(copy_button)
                identifiers.addLayout(session_row)
            else:
                identifiers.addWidget(field)

        calls = section("inspectorComposedCalls")
        if grouped:
            if self._queries:
                summaries = self._queries.request_composition(record["id"])
            else:
                summaries = summarize_model_calls(self._records_by_id[key] for key in record.get("member_ids", []) if key in self._records_by_id)
            total = sum(row["call_count"] for row in summaries)
            calls.addWidget(plain_label("调用组成 · %s" % total))
            for value in summaries:
                row = QFrame()
                row.setProperty("callSummary", True)
                row_layout = QVBoxLayout(row)
                row_layout.setContentsMargins(0, 4, 0, 10)
                row_layout.setSpacing(6)
                model = plain_label("%s × %s" % (value["model"], value["call_count"]), wrap=True)
                model.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                row_layout.addWidget(model)
                amount = QHBoxLayout()
                amount.addWidget(plain_label(tier_label(value), muted=True))
                price_text = "未定价" if value["pricing_status"] == "unpriced" else request_cost_text(value)
                price = plain_label(price_text, wrap=True)
                price.setAlignment(Qt.AlignmentFlag.AlignRight)
                amount.addWidget(price, 1)
                row_layout.addLayout(amount)
                calls.addWidget(row)
        else:
            parent = (self._queries.request_for_record(record["id"]) if self._queries and hasattr(self._queries, "request_for_record") else
                      next((row for row in self._user_requests if record.get("id") in row.get("member_ids", [])), None))
            if parent and parent.get("record_kind") == "user_request":
                back = QPushButton("所属用户请求")
                back.setProperty("quiet", True)
                back.clicked.connect(lambda checked=False, value=parent: self._show_inspector(value))
                calls.addWidget(back, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        self._inspector_scroll.setWidget(content)
        self._inspector_stack.setCurrentWidget(self._inspector_scroll)
        self._escape_inspector.setEnabled(True)
        if old_scroll is not None:
            self._inspector_scroll.verticalScrollBar().setValue(old_scroll)
        if focus:
            self._log_drawer.inspector.setFocus(Qt.FocusReason.OtherFocusReason)

    def _close_inspector(self, *args, restore_focus=True):
        """Clear details while keeping the right pane in place."""
        if restore_focus:
            self._log_preview_dismissed = True
        self._escape_inspector.setEnabled(False)
        self._inspected_record = None
        self._inspector_duration = None
        self._inspector_origin = None
        self._inspector_heading.set_text("请求详情")
        self._inspector_heading.setToolTip("")
        previous = self._inspector_scroll.takeWidget()
        if previous:
            previous.hide()
            previous.deleteLater()
        self._inspector_stack.setCurrentWidget(self._inspector_empty)
        self._log_table.clearSelection()
        self._log_table.setCurrentCell(-1, -1)
        if restore_focus:
            self._log_table.setFocus()

    def _preview_widget(self, *args):
        values = {key: self._control_value(widget) for key, widget in self._setting_widgets.items()}
        self._widget_preview.configure(replace(self._settings, **values).normalized(), self._quota_state)
