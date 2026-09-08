"""The application's native main window. Data acquisition belongs to its controller."""
from __future__ import annotations

import copy
import math
import re
import uuid
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Optional

from PySide6.QtCore import QDate, QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QDateEdit,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from aiquota import __version__
from aiquota.charts import UsageChart, compact_number, parse_timestamp, period_bounds
from aiquota.settings import AppSettings
from aiquota.durations import duration_text, duration_tooltip
from aiquota.rate_limits import format_reset_time
from aiquota.theme import apply_theme, theme_colors
from aiquota.user_requests import aggregate_user_requests, matches_call, REQUEST_STATUSES

PAGE_NAMES = ("overview", "trends", "logs", "pricing", "settings")
PAGE_LABELS = ("概览", "用量趋势", "请求日志", "模型定价", "设置")
PERIODS = (("今日", "today"), ("近 7 天", "week"), ("近 30 天", "month"), ("全部", "all"))
PRICE_STATUS_LABELS = {"priced": "已定价", "unpriced": "未定价", "estimated": "标准价估算", "invalid": "计量分项异常",
                       "partial": "部分未定价", "unmetered": "待计量"}
CALL_HEADERS = ["时间", "模型", "Fast", "输入", "输出", "费用", "耗时", "Session ID", "来源"]
USER_REQUEST_HEADERS = CALL_HEADERS[:-1] + ["状态", "来源"]
DURATION_COLUMN = CALL_HEADERS.index("耗时")
SESSION_COLUMN = CALL_HEADERS.index("Session ID")
STATUS_COLUMN = USER_REQUEST_HEADERS.index("状态")
ESTIMATE_STATUSES = {
    "ready": ("已估算", "按已记录用量折算整周额度，金额供参考。"),
    "unattributed": ("待确认账号", "请在设置中确认这段历史记录所属的账号。"),
    "unknown_plan": ("套餐未识别", "缺少这段记录的订阅套餐信息。"),
    "unknown_limit": ("额度类型待确认", "部分请求还无法对应到周额度。"),
    "incomplete": ("用量待补齐", "等待日志同步完整后重新估算。"),
    "unpriced": ("价格待补齐", "部分请求尚无可用价格。"),
    "estimated_prices": ("计价信息不足", "部分请求缺少速度模式等计价信息。"),
    "collecting": ("继续采样", "等待足够的用量记录与额度变化。"),
    "insufficient": ("继续采样", "额度变化达到 5 个百分点后开始估算。"),
}


def estimate_status(value: dict) -> tuple[str, str]:
    status = value.get("status") or ("ready" if value.get("estimated_total_usd") is not None else "insufficient")
    return ESTIMATE_STATUSES.get(status, ("暂不可用", "等待完整的用量与额度数据。"))


def estimate_interval(value: dict) -> tuple[str, str]:
    start, end = parse_timestamp(value.get("start")), parse_timestamp(value.get("end"))
    if start is None or end is None:
        return "—", "采样时间未记录"
    text = start.strftime("%m/%d %H:%M") + "\n→ " + end.strftime("%m/%d %H:%M")
    return text, start.strftime("%Y/%m/%d %H:%M") + " → " + end.strftime("%Y/%m/%d %H:%M")


def fast_mode_label(record: dict) -> str:
    tier = str(record.get("service_tier") or "").lower()
    if tier in ("priority", "fast"):
        return "开启"
    if tier in ("default", "standard"):
        return "关闭"
    if tier == "mixed":
        return "混合"
    return "未知"


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


def usd(value: object, precision: int = 2) -> str:
    if value is None:
        return "未定价"
    try:
        number = float(value)
        if 0 < number < 10 ** (-precision):
            return "<$%s" % format(10 ** (-precision), ".%df" % precision)
        return "$%s" % format(number, ",.%df" % precision)
    except (TypeError, ValueError):
        return "未定价"


def request_cost_text(record: dict, precision: int = 5) -> str:
    status = record.get("pricing_status")
    if status == "unmetered":
        return "待计量"
    value = usd(record.get("cost_usd"), precision)
    if status == "partial":
        return value + "\n部分未定价"
    if status == "estimated":
        return value + "\n估算"
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
            if name in ("Fast", "状态"):
                values = ("开启", "关闭", "未知", "混合") if name == "Fast" else tuple(REQUEST_STATUSES.values())
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

    def __init__(self, text: str, parent: Optional[QWidget] = None, max_lines: int = 2) -> None:
        super().__init__(parent)
        self.max_lines = max(1, int(max_lines))
        self.text = " ".join(str(text).split())
        self.setMinimumHeight(QFontMetrics(self.font()).lineSpacing() * self.max_lines + 2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(self.text)
        self.last_line_count = 0

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
            self.setFixedHeight(QFontMetrics(self.font()).lineSpacing() * self.max_lines + 2)
        super().changeEvent(event)


TwoLineText = LineLimitedText  # Compatibility for title previews and callers.


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
        view.item(index, 5).setToolTip(request_cost_text(row, 6) + "\n" + str(row.get("pricing_reason") or ""))
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
            amount = plain_label(request_cost_text(record, 6), wrap=True)
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
            amount = plain_label(request_cost_text(record, 6), wrap=True)
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
        layout.addWidget(plain_label("将指定来源与日期范围的历史计量归属到当前账号，用于周额度估算。", muted=True, wrap=True))
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
        self.start = QDateEdit(QDate(earliest.year, earliest.month, earliest.day))
        self.end = QDateEdit(QDate.currentDate())
        for field in (self.start, self.end):
            field.setCalendarPopup(True)
            field.setDisplayFormat("yyyy-MM-dd")
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


class PriceEditor(QDialog):
    def __init__(self, price: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑模型价格 · 美元 / 百万 Token")
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.model = QLineEdit(str(price.get("model") or ""))
        form.addRow("模型 ID", self.model)
        self.inputs: dict[str, QDoubleSpinBox] = {}
        for key, label in (("input", "普通输入"), ("cache_read", "缓存读取"), ("cache_write", "缓存创建"), ("output", "输出")):
            spin = QDoubleSpinBox()
            spin.setRange(0, 1000000)
            spin.setDecimals(6)
            spin.setSingleStep(0.01)
            spin.setValue(float(price.get(key) or 0))
            self.inputs[key] = spin
            form.addRow(label, spin)
        self.tier = combo((("Standard", "default"), ("Fast / Priority", "priority")), price.get("service_tier") or "default")
        form.addRow("服务档位", self.tier)
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 10000000)
        self.threshold.setSingleStep(1000)
        self.threshold.setValue(int(price.get("threshold") or 0))
        self.threshold.setSpecialValueText("普通上下文")
        form.addRow("输入超过阈值", self.threshold)
        layout.addLayout(form)
        layout.addWidget(plain_label("手工价格会锁定该项，自动同步保留你的设置。", muted=True, wrap=True))
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
        self.accept()

    def rates(self) -> dict:
        return {**{key: item.value() for key, item in self.inputs.items()},
                "service_tier": self.tier.currentData(), "threshold": self.threshold.value(),
                "locked": True}


class Dashboard(QMainWindow):

    def __init__(self, settings: AppSettings, analytics_config: dict, callbacks: dict) -> None:
        super().__init__()
        self._settings = settings.normalized()
        self._config = copy.deepcopy(analytics_config)
        self._callbacks = callbacks
        self._theme = str(self._config.get("theme") or "system")
        self._data: dict = {}
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
        self.setWindowTitle("AIQuota · Codex 用量中心")
        self.resize(1190, 800)
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
        sidebar.setFixedWidth(192)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 26, 18, 22)
        side.setSpacing(7)
        brand = plain_label("AIQuota")
        brand.setProperty("heading", True)
        side.addWidget(brand)
        side.addWidget(plain_label("CODEX 用量中心", muted=True))
        side.addSpacing(30)
        self._nav: dict[str, QPushButton] = {}
        for name, label in zip(PAGE_NAMES, PAGE_LABELS):
            button = QPushButton(label)
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, page=name: self.open_page(page))
            side.addWidget(button)
            self._nav[name] = button
        side.addStretch()
        self._sidebar_status = plain_label("正在读取本机记录", muted=True, wrap=True)
        side.addWidget(self._sidebar_status)
        side.addSpacing(10)
        self._widget_toggle = QCheckBox("显示悬浮窗")
        self._widget_toggle.toggled.connect(self._toggle_widget)
        side.addWidget(self._widget_toggle)
        layout.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(30, 27, 30, 24)
        main.setSpacing(20)
        header = QHBoxLayout()
        headings = QVBoxLayout()
        headings.setSpacing(5)
        self._page_title = plain_label("概览")
        self._page_title.setProperty("heading", True)
        self._page_subtitle = plain_label("额度、Token 与美元费用，一处看清。", muted=True)
        headings.addWidget(self._page_title)
        headings.addWidget(self._page_subtitle)
        header.addLayout(headings, 1)
        refresh = QPushButton("刷新数据")
        refresh.clicked.connect(lambda: self._callback("refresh"))
        header.addWidget(refresh)
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
        self._footer = plain_label("更新时间 —", muted=True)
        main.addWidget(self._footer)
        layout.addLayout(main, 1)

    def _page(self, scroll: bool = False) -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        content.setObjectName("page")
        inner = QVBoxLayout(content)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(18)
        if not scroll:
            return content, inner
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(content)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return area, inner

    def _build_overview(self) -> QWidget:
        page, layout = self._page(scroll=True)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        metrics = QGridLayout()
        metrics.setSpacing(14)
        self._metrics = {}
        for index, (label, key) in enumerate(PERIODS):
            box, content = card()
            box.setProperty("tone", ("blue", "teal", "violet", "gold")[index])
            content.addWidget(plain_label(label + " Token", muted=True))
            value = plain_label("—")
            value.setProperty("metric", True)
            cost = plain_label("等待统计")
            cost.setProperty("money", True)
            note = plain_label("", muted=True)
            content.addWidget(value)
            content.addWidget(cost)
            content.addWidget(note)
            metrics.addWidget(box, 0, index)
            self._metrics[key] = (value, cost, note)
        layout.addLayout(metrics)
        middle = QHBoxLayout()
        middle.setSpacing(16)
        quota_box, quota_content = card()
        heading = plain_label("会员额度")
        heading.setProperty("subheading", True)
        quota_content.addWidget(heading)
        self._quota_plan = plain_label("读取中", muted=True)
        quota_content.addWidget(self._quota_plan)
        self._quota_labels = {}
        self._quota_resets = {}
        for key, title in (("five_hour", "5 小时"), ("week", "本周")):
            head = QHBoxLayout()
            head.addWidget(plain_label(title))
            reset = plain_label("重置 —", muted=True)
            reset.setAlignment(Qt.AlignmentFlag.AlignCenter)
            reset.setStyleSheet("font-size: 12px;")
            head.addWidget(reset, 1)
            label = plain_label("—")
            head.addWidget(label)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            quota_content.addLayout(head)
            quota_content.addWidget(bar)
            self._quota_labels[key] = (label, bar, title)
            self._quota_resets[key] = reset
        self._quota_message = plain_label("", muted=True, wrap=True)
        quota_content.addWidget(self._quota_message)
        middle.addWidget(quota_box, 2)
        self._reset_box, reset_content = card()
        self._reset_box.setProperty("tone", "teal")
        heading = plain_label("可用重置次数")
        heading.setProperty("subheading", True)
        reset_content.addWidget(heading)
        self._reset_count = plain_label("—")
        self._reset_count.setProperty("metric", True)
        reset_content.addStretch()
        reset_content.addWidget(self._reset_count)
        reset_content.addStretch()
        middle.addWidget(self._reset_box, 1)
        estimate_box, estimate_content = card()
        estimate_box.setProperty("tone", "gold")
        heading = plain_label("整周额度估值")
        heading.setProperty("subheading", True)
        estimate_content.addWidget(heading)
        self._estimate_value = plain_label("待估算")
        self._estimate_value.setProperty("money", True)
        estimate_content.addWidget(self._estimate_value)
        self._estimate_note = plain_label("", muted=True, wrap=True)
        estimate_content.addWidget(self._estimate_note)
        self._estimate_period = plain_label("", muted=True, wrap=True)
        estimate_content.addWidget(self._estimate_period)
        details = QPushButton("估值记录")
        details.clicked.connect(self._show_estimates)
        estimate_content.addWidget(details, alignment=Qt.AlignmentFlag.AlignLeft)
        middle.addWidget(estimate_box, 1)
        layout.addLayout(middle)
        graph, content = card()
        row = QHBoxLayout()
        label = plain_label("近 7 天使用趋势")
        label.setProperty("subheading", True)
        row.addWidget(label, 1)
        more = QPushButton("展开趋势")
        more.clicked.connect(lambda: self.open_page("trends"))
        row.addWidget(more)
        content.addLayout(row)
        self._overview_chart = UsageChart()
        content.addWidget(self._overview_chart)
        self._latest_box, self._latest_content = card()
        latest_label = plain_label("最近一次用户请求")
        latest_label.setProperty("subheading", True)
        self._latest_content.addWidget(latest_label)
        self._latest_panel = plain_label("正在加载请求记录", muted=True)
        self._latest_content.addWidget(self._latest_panel)
        self._latest_record = object()
        layout.addWidget(self._latest_box)
        layout.addWidget(graph)
        return page

    def _build_trends(self) -> QWidget:
        page, layout = self._page()
        controls = QHBoxLayout()
        self._trend_period = combo(PERIODS + (("自选日期", "custom"),), "week")
        self._granularity = combo((("每小时", "hour"), ("每天", "day"), ("每周", "week")), "day")
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
        self._trend_start = QDateEdit(QDate.currentDate().addDays(-6))
        self._trend_end = QDateEdit(QDate.currentDate())
        for widget in (self._trend_start, self._trend_end):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
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
        layout.addWidget(graph, 1)
        self._trend_note = plain_label("", muted=True, wrap=True)
        self._trend_note.hide()
        layout.addWidget(self._trend_note)
        return page

    def _build_logs(self) -> QWidget:
        page, layout = self._page()
        mode = QHBoxLayout()
        mode.addWidget(plain_label("统计方式"))
        self._log_mode = combo((("按用户请求", "user_request"), ("按模型调用", "model_call")), "user_request")
        self._log_mode.currentIndexChanged.connect(self._filters_changed)
        mode.addWidget(self._log_mode)
        self._request_filter_hint = plain_label("按发起日期归属 · 费用为整轮累计", muted=True)
        mode.addWidget(self._request_filter_hint)
        mode.addStretch()
        layout.addLayout(mode)
        controls = QHBoxLayout()
        self._log_period = combo(PERIODS + (("自选日期", "custom"),), "today")
        self._log_source = combo((("所有来源", ""),))
        self._log_model = combo((("所有模型", ""),))
        self._log_tier = combo((("所有档位", ""), ("Standard", "default"), ("Fast / Priority", "priority"), ("未记录", "unknown")))
        for widget in (self._log_period, self._log_source, self._log_model, self._log_tier):
            widget.currentIndexChanged.connect(self._filters_changed)
            controls.addWidget(widget)
        controls.addStretch()
        layout.addLayout(controls)
        dates = QHBoxLayout()
        self._date_start = QDateEdit(QDate.currentDate())
        self._date_end = QDateEdit(QDate.currentDate())
        for widget in (self._date_start, self._date_end):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
            widget.dateChanged.connect(self._filters_changed)
        dates.addWidget(plain_label("从"))
        dates.addWidget(self._date_start)
        dates.addWidget(plain_label("至"))
        dates.addWidget(self._date_end)
        dates.addStretch()
        self._date_row = QWidget()
        self._date_row.setLayout(dates)
        self._date_row.hide()
        layout.addWidget(self._date_row)
        self._log_table = CompactLogTable(USER_REQUEST_HEADERS)
        self._log_table.cellDoubleClicked.connect(self._show_request_row)
        layout.addWidget(self._log_table, 1)
        pagination = QHBoxLayout()
        pagination.setContentsMargins(0, 0, 0, 0)
        self._log_count = plain_label("暂无请求", muted=True)
        pagination.addWidget(self._log_count, 1)
        self._previous = QPushButton("上一页")
        self._next = QPushButton("下一页")
        self._page_indicator = plain_label("1 / 1")
        self._previous.clicked.connect(lambda: self._change_page(-1))
        self._next.clicked.connect(lambda: self._change_page(1))
        pagination.addWidget(self._previous)
        pagination.addWidget(self._page_indicator)
        pagination.addWidget(self._next)
        self._log_pagination = QWidget()
        self._log_pagination.setLayout(pagination)
        layout.addWidget(self._log_pagination)
        return page

    def _build_pricing(self) -> QWidget:
        page, layout = self._page()
        box, content = card()
        row = QHBoxLayout()
        text = QVBoxLayout()
        heading = plain_label("模型价格（美元 / 1M Token）")
        heading.setProperty("subheading", True)
        text.addWidget(heading)
        self._price_status = plain_label("内置价格 · 等待同步状态", muted=True, wrap=True)
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
        layout.addWidget(box)
        controls = QHBoxLayout()
        self._price_search = QLineEdit()
        self._price_search.setPlaceholderText("搜索模型…")
        self._price_search.textChanged.connect(self._update_prices)
        controls.addWidget(self._price_search, 1)
        add = QPushButton("添加手工价格")
        add.clicked.connect(lambda: self._edit_price({}))
        edit = QPushButton("编辑所选")
        edit.clicked.connect(self._edit_selected_price)
        reset = QPushButton("恢复自动价格")
        reset.clicked.connect(self._reset_selected_price)
        for button in (add, edit, reset):
            controls.addWidget(button)
        layout.addLayout(controls)
        self._price_table = table(["模型", "档位 / 输入阈值", "输入", "缓存读取", "缓存创建", "输出"])
        header = self._price_table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents if column < 2 else QHeaderView.ResizeMode.Stretch)
            self._price_table.horizontalHeaderItem(column).setTextAlignment(
                (Qt.AlignmentFlag.AlignLeft if column < 2 else Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter)
        self._price_table.cellDoubleClicked.connect(lambda *_: self._edit_selected_price())
        layout.addWidget(self._price_table, 1)
        return page

    def _build_settings(self) -> QWidget:
        page, layout = self._page(scroll=True)
        self._setting_widgets: dict[str, QWidget] = {}
        updates = QGroupBox("应用更新")
        update_layout = QVBoxLayout(updates)
        update_row = QHBoxLayout()
        update_row.addWidget(plain_label("当前版本 " + __version__))
        update_row.addStretch()
        self._check_update = QPushButton("检查并更新")
        self._check_update.clicked.connect(lambda: self._callback("check_update"))
        update_row.addWidget(self._check_update)
        update_layout.addLayout(update_row)
        self._auto_update = QCheckBox("自动下载更新")
        self._auto_update.toggled.connect(self._set_auto_update)
        update_layout.addWidget(self._auto_update)
        self._update_status = plain_label("启动后检查新版；更新完成后自动重启。", muted=True, wrap=True)
        update_layout.addWidget(self._update_status)
        layout.addWidget(updates)
        appearance = QGroupBox("界面与悬浮窗")
        form = QFormLayout(appearance)
        form.setVerticalSpacing(12)
        self._theme_combo = combo((("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")), self._theme)
        form.addRow("主界面主题", self._theme_combo)
        fields = [("display_mode", "显示模式", (("始终置顶", "top"), ("桌面底层", "bottom"), ("仅托盘", "tray"))),
                  ("visual_style", "悬浮窗样式", (("经典", "classic"), ("双环", "rings"), ("卡片", "tiles"), ("紧凑", "compact"), ("极简", "minimal"), ("光球", "orb"))),
                  ("quota_scope", "额度范围", (("自动", "auto"), ("5 小时与周额度", "both"), ("仅周额度", "week"))),
                  ("refresh_interval_seconds", "额度刷新", (("30 秒", 30), ("1 分钟", 60), ("5 分钟", 300))),
                  ("dock_edge", "贴边停靠", (("不贴边", "none"), ("顶部", "top"), ("底部", "bottom"), ("左侧", "left"), ("右侧", "right")))]
        for key, label, choices in fields:
            field = combo(choices, getattr(self._settings, key))
            self._setting_widgets[key] = field
            form.addRow(label, field)
        for key, label in (("background_transparent", "背景透明"), ("show_border", "显示边框")):
            field = QCheckBox(label)
            field.setChecked(bool(getattr(self._settings, key)))
            self._setting_widgets[key] = field
            form.addRow(field)
        opacity = QSpinBox()
        opacity.setRange(0, 100)
        opacity.setSuffix(" %")
        opacity.setValue(self._settings.background_opacity)
        self._setting_widgets["background_opacity"] = opacity
        form.addRow("透明度", opacity)
        border = QLineEdit(self._settings.border_color)
        self._setting_widgets["border_color"] = border
        form.addRow("边框颜色", border)
        codex_path = QLineEdit(self._settings.codex_path or "")
        codex_path.setPlaceholderText("自动发现 Codex")
        self._setting_widgets["codex_path"] = codex_path
        form.addRow("Codex 可执行文件", codex_path)
        layout.addWidget(appearance)

        sources = QGroupBox("Token 数据来源")
        source_layout = QVBoxLayout(sources)
        source_layout.addWidget(plain_label("管理本机目录和 SSH 主机的用量记录。", muted=True, wrap=True))
        self._source_list = QListWidget()
        self._source_list.setMinimumHeight(145)
        self._source_list.itemDoubleClicked.connect(lambda *_: self._edit_source())
        source_layout.addWidget(self._source_list)
        source_buttons = QHBoxLayout()
        for kind, label in (("local", "+ 本机目录"), ("ssh", "+ SSH")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, source_kind=kind: self._edit_source(source_kind))
            source_buttons.addWidget(button)
        remove = QPushButton("移除所选")
        remove.clicked.connect(self._remove_source)
        source_buttons.addWidget(remove)
        source_buttons.addStretch()
        source_layout.addLayout(source_buttons)
        self._sources_status = plain_label("", muted=True, wrap=True)
        source_layout.addWidget(self._sources_status)
        layout.addWidget(sources)

        account = QGroupBox("历史归属与索引")
        history_layout = QVBoxLayout(account)
        self._account_since = plain_label("", muted=True, wrap=True)
        history_layout.addWidget(self._account_since)
        row = QHBoxLayout()
        assign = QPushButton("归属本机历史到当前账号")
        assign.clicked.connect(self._assign_history)
        rescan = QPushButton("重新扫描计量记录")
        rescan.clicked.connect(lambda: self._callback("rescan"))
        row.addWidget(assign)
        row.addWidget(rescan)
        row.addStretch()
        history_layout.addLayout(row)
        layout.addWidget(account)
        save_row = QHBoxLayout()
        self._settings_message = plain_label("", muted=True)
        save_row.addWidget(self._settings_message, 1)
        save = QPushButton("保存设置")
        save.setProperty("primary", True)
        save.clicked.connect(self._save_settings)
        save_row.addWidget(save)
        layout.addLayout(save_row)
        layout.addStretch()
        return page

    @staticmethod
    def _control_value(widget):
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
            if isinstance(widget, QComboBox):
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
            "logs": ("_log_mode", "_log_period", "_log_source", "_log_model", "_log_tier", "_date_start", "_date_end"),
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
                             selected=rows[selected].get("id") if 0 <= selected < len(rows) else None)
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
            self._request_filter_hint.setVisible(self._log_mode.currentData() == "user_request")
            self._date_row.setVisible(self._log_period.currentData() == "custom")
        elif name == "trends":
            self._trend_date_row.setVisible(self._trend_period.currentData() == "custom")
        elif name == "settings":
            for key, value in state.get("draft", {}).items():
                if key in self._setting_widgets:
                    self._restore_control(self._setting_widgets[key], value)
            if "theme_draft" in state:
                self._restore_control(self._theme_combo, state["theme_draft"])

    def _apply_requested_period(self, name: str) -> None:
        period = self._period_overrides.pop(name, None)
        if period is None:
            return
        field = self._log_period if name == "logs" else self._trend_period
        self._restore_control(field, period)
        if name == "logs":
            self._page_number = 0
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
                item = self._log_table.item(index, DURATION_COLUMN)
                if item is not None:
                    text = duration_text(row, now)
                    if item.text() != text:
                        item.setText(text)
                        item.setToolTip(text + "\n" + duration_tooltip(row))
                        if self._log_table.fontMetrics().horizontalAdvance(text) + 16 > self._log_table.columnWidth(DURATION_COLUMN):
                            self._log_table.queue_columns()

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
                if self._data:
                    self._render_overview()
                if self._quota_state is not None:
                    self._render_quota(self._quota_state)
                if self._usage_error and not self._data:
                    for value, cost, note in self._metrics.values():
                        value.setText("—")
                        cost.setText("未加载")
                        note.clear()
                    if isinstance(self._latest_panel, QLabel):
                        self._latest_panel.setText("请求记录加载失败")
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
        self._update_startup_progress()

    def set_update_status(self, message: str, busy: bool = False) -> None:
        self._update_message = message, busy
        self._dirty_pages.add("settings")
        self._refresh_visible()

    def apply_data(self, data: dict) -> None:
        self._data = data if isinstance(data, dict) else {}
        query_path = self._data.get("query_path")
        if query_path:
            from aiquota.usage_queries import UsageQueries
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
        for key, widgets in self._metrics.items():
            summary = self._data.get("summaries", {}).get(key, {})
            widgets[0].setText(compact_number(summary.get("tokens", 0)))
            widgets[1].setText(usd(summary.get("usd", 0)))
            unpriced = int(summary.get("unpriced_tokens") or 0)
            widgets[2].setText(("%s Token 未定价" % compact_number(unpriced)) if unpriced else "%s 次模型调用" % format(int(summary.get("requests") or 0), ","))
        if self._queries:
            self._overview_chart.set_buckets(self._queries.chart_buckets("week", "day"), "day")
        else:
            self._overview_chart.set_records(self._records, "week", "day")
        self._update_estimate()
        latest = (self._data.get("latest_request") if self._queries else
                  next((row for row in self._user_requests if row.get("record_kind") == "user_request" and not row.get("is_subagent")), None))
        if latest != self._latest_record:
            self._latest_content.removeWidget(self._latest_panel)
            self._latest_panel.hide()
            self._latest_panel.deleteLater()
            self._latest_panel = (RequestContent(latest, self._theme, self._latest_box, compact=True) if latest
                                  else plain_label("暂无可识别的用户请求。可在设置中添加 Codex 数据来源。", muted=True))
            self._latest_content.addWidget(self._latest_panel)
            self._latest_record = copy.deepcopy(latest)
    def apply_quota(self, state) -> None:
        self._quota_state = state
        status = state.get("status") if isinstance(state, dict) else getattr(state, "status", None)
        status = getattr(status, "value", status)
        if status in ("ok", "error"):
            self._initial_quota_done = True
        if self._page_is_active("overview"):
            self._render_quota(state)
        else:
            self._dirty_pages.add("overview")
        if self.isVisible() and not self.isMinimized():
            self._update_startup_progress()

    def _render_quota(self, state) -> None:
        def get(obj, key, default=None):
            return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
        plan = get(state, "plan_type") or "会员计划"
        self._quota_plan.setText(str(plan).upper())
        for key, (label, bar, title) in self._quota_labels.items():
            window = get(state, key)
            remaining = get(window, "remaining_percent")
            label.setText("剩余 %s%%" % remaining if remaining is not None else "暂不可用")
            bar.setValue(round(float(remaining)) if remaining is not None else 0)
            resets = get(window, "resets_at")
            self._quota_resets[key].setText("重置 " + (format_reset_time(resets) if isinstance(resets, datetime) else "—"))
        credits = get(state, "reset_credits")
        self._reset_count.setText("%s 次" % credits if isinstance(credits, int) and not isinstance(credits, bool) and credits >= 0 else "—")
        self._quota_message.setText(str(get(state, "message") or ""))
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
        self._page_title.setText(PAGE_LABELS[index])
        subtitles = ("额度、Token 与美元费用，一处看清。", "按小时、天或周查看使用变化。", "按用户请求或模型调用查看 Token 和等价费用。", "查看、同步与调整模型的计价方式。", "管理外观与数据来源。")
        self._page_subtitle.setText(subtitles[index])
        for key, button in self._nav.items():
            button.setChecked(key == name)
        if name in ("logs", "trends") and period is not None:
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

    def hideEvent(self, event) -> None:
        self._duration_timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_duration_timer"):
            self._sync_duration_timer()
            self._refresh_visible()

    def config_updated(self, config: dict) -> None:
        self._config = copy.deepcopy(config)
        self._theme = str(self._config.get("theme") or "system")
        self._widget_toggle.blockSignals(True)
        self._widget_toggle.setChecked(bool(config.get("widget_visible", True)))
        self._widget_toggle.blockSignals(False)
        self._config_dirty.update(PAGE_NAMES)
        self._dirty_pages.update(PAGE_NAMES)
        self._apply_theme()
        self._refresh_visible()

    def _sync_config_controls(self, name: str) -> None:
        if name == "pricing":
            self._auto_sync.blockSignals(True)
            self._auto_sync.setChecked(bool(self._config.get("auto_sync_prices", True)))
            self._auto_sync.blockSignals(False)
        elif name == "settings":
            self._auto_update.blockSignals(True)
            self._auto_update.setChecked(bool(self._config.get("auto_update", True)))
            self._auto_update.blockSignals(False)
            self._theme_combo.setCurrentIndex(max(0, self._theme_combo.findData(self._theme)))
            self._account_since.setText("当前账号观测起点：" + (str(self._config.get("account_since")) if self._config.get("account_since") else "首次成功读取额度后记录"))
            self._refresh_source_list()
        self._config_dirty.discard(name)

    def _apply_theme(self) -> None:
        apply_theme(self, self._theme)
        if "pricing" in self._pages:
            self._size_price_columns()
        if "logs" in self._pages:
            self._log_table.queue_columns()
        for name, attribute in (("overview", "_overview_chart"), ("trends", "_trend_chart")):
            if name in self._pages:
                getattr(self, attribute).set_theme(self._theme)

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
        pairs = [(self._log_model, "所有模型", models),
                 (self._log_source, "所有来源", sorted(source_ids))]
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
        if hasattr(self, "_request_filter_hint"):
            self._request_filter_hint.setVisible(self._log_mode.currentData() == "user_request")
        if hasattr(self, "_date_row"):
            self._date_row.setVisible(self._log_period.currentData() == "custom")
        if hasattr(self, "_log_table"):
            self._filter_records()

    def _filter_records(self, reset_page: bool = True) -> None:
        if not self._page_is_active("logs"):
            self._dirty_pages.add("logs")
            return
        period = self._log_period.currentData()
        lower, upper = period_bounds(period)
        if period == "custom":
            zone = datetime.now().astimezone().tzinfo
            lower = datetime.combine(self._date_start.date().toPython(), time.min, tzinfo=zone)
            upper = datetime.combine(self._date_end.date().toPython(), time.max, tzinfo=zone)
        source, model, tier = self._log_source.currentData(), self._log_model.currentData(), self._log_tier.currentData()
        if self._queries:
            self._query_filters = dict(mode=self._log_mode.currentData(), start=lower, end=upper,
                                       source=source or "", model=model or "", tier=tier or "")
            if reset_page:
                self._page_number = 0
            self._render_log_page()
            return
        rows = []
        grouped = self._log_mode.currentData() == "user_request"
        for row in (self._user_requests if grouped else self._records):
            stamp = parse_timestamp(row.get("timestamp"))
            if stamp is None or stamp > upper or (lower and stamp < lower):
                continue
            if grouped:
                members = [self._records_by_id[ident] for ident in row.get("member_ids", []) if ident in self._records_by_id]
                if members:
                    if not any(matches_call(member, source, model, tier) for member in members):
                        continue
                elif model or tier or source and source not in row.get("source_ids", []):
                    continue
            elif not matches_call(row, source, model, tier):
                continue
            rows.append(row)
        self._filtered_records = rows
        if reset_page:
            self._page_number = 0
        self._render_log_page()

    def _render_log_page(self) -> None:
        grouped = self._log_mode.currentData() == "user_request"
        if self._log_table.set_headers(USER_REQUEST_HEADERS if grouped else CALL_HEADERS):
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
            return
        previous_rows = getattr(self, "_rendered_log_rows", None) or []
        selected = self._log_table.currentRow()
        selected_id = previous_rows[selected].get("id") if 0 <= selected < len(previous_rows) else self.__dict__.pop("_saved_log_selection", None)
        scroll = self.__dict__.pop("_saved_log_scroll", self._log_table.verticalScrollBar().value())
        self._rendered_log_rows = rows if self._queries else copy.deepcopy(rows)
        populate_request_table(self._log_table, rows, grouped)
        if selected_id:
            for index, row in enumerate(rows):
                if row.get("id") == selected_id:
                    self._log_table.selectRow(index)
                    break
        self._log_table.verticalScrollBar().setValue(scroll)
        self._update_log_navigation(count, page_count)

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
            model = self._trend_model.currentData()
            rows = [row for row in self._records if not model or row.get("model") == model]
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
            if hasattr(self, "_trend_note"):
                self._trend_note.clear()
                self._trend_note.hide()

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
        rows = [p for p in self._data.get("prices", []) if isinstance(p, dict) and p.get("model") in allowed
                and p.get("service_tier", "default") in ("default", "standard", "priority")]
        priced_models = {p["model"] for p in rows}
        rows.extend({"model": model, "service_tier": "default", "threshold": 0}
                    for model in available if model not in priced_models)
        order = {model: index for index, model in enumerate(available)}
        rows.sort(key=lambda p: (order[p["model"]], str(p.get("service_tier", "default")), int(p.get("threshold") or 0)))
        self._visible_prices = [p for p in rows if search in str(p.get("model") or "").lower()]
        self._price_table.setRowCount(len(self._visible_prices))
        for index, price in enumerate(self._visible_prices):
            context = " >%s" % compact_number(price["threshold"]) if price.get("threshold") else ""
            tier = {"default": "Standard", "standard": "Standard", "priority": "Fast"}.get(price.get("service_tier") or "default", "Standard")
            values = [price.get("model") or "—", tier + context,
                      *[str(Decimal(str(price[k])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        if price.get(k) is not None else "未定价" for k in ("input", "cache_read", "cache_write", "output")]]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setTextAlignment((Qt.AlignmentFlag.AlignLeft if column < 2 else Qt.AlignmentFlag.AlignRight) | Qt.AlignmentFlag.AlignVCenter)
                if column == 0 and price.get("locked"):
                    item.setToolTip("手工锁定价格")
                self._price_table.setItem(index, column, item)
        self._size_price_columns()
        status = self._data.get("pricing_status") or {}
        if isinstance(status, dict):
            label = {"bundled": "价格已就绪", "synced": "价格已同步", "fallback": "价格已同步", "offline": "同步失败，沿用已有价格", "conflict": "部分价格更新待核验"}.get(status.get("status"), "价格已就绪")
            stamp = parse_timestamp(status.get("updated_at") or status.get("last_sync_at"))
            catalog_status = self._data.get("model_catalog_status") or {}
            note = "仅显示本机 Codex 提供的 %d 个模型" % len(available)
            if not available:
                note = "暂未读取到本机 Codex 模型列表"
            elif catalog_status.get("status") == "stale":
                note += "，当前使用上次读取的列表"
            self._price_status.setText(note + "\n" + label + (" · " + stamp.strftime("%m/%d %H:%M") if stamp else ""))

    def _size_price_columns(self) -> None:
        if not hasattr(self, "_price_table"):
            return
        header = self._price_table.horizontalHeader()
        metrics = header.fontMetrics()
        minimum = max(metrics.horizontalAdvance(self._price_table.horizontalHeaderItem(column).text()) + 30
                      for column in range(2, 6))
        header.setMinimumSectionSize(max(82, minimum))

    def _edit_price(self, price: dict) -> None:
        dialog = PriceEditor(price, self)
        apply_theme(dialog, self._theme)
        dialog.accepted.connect(lambda: self._callback("price_override", dialog.model.text().strip(), dialog.rates()))
        self._dialog(dialog)

    def _edit_selected_price(self) -> None:
        row = self._price_table.currentRow()
        if 0 <= row < len(self._visible_prices):
            self._edit_price(self._visible_prices[row])

    def _reset_selected_price(self) -> None:
        row = self._price_table.currentRow()
        if 0 <= row < len(self._visible_prices):
            self._callback("price_override", self._visible_prices[row]["model"], None)

    def _update_estimate(self) -> None:
        estimates = self._data.get("weekly_estimates") or []
        value = estimates[0] if estimates else {}
        amount = value.get("estimated_total_usd")
        status, reason = estimate_status(value)
        self._estimate_value.setText(usd(amount) if amount is not None else "待估算")
        self._estimate_value.setToolTip("周额度用满时折合的预计金额（美元）。\n" + reason)
        delta = value.get("delta_percent")
        self._estimate_note.setText("已采样 %g 个百分点" % float(delta) if delta is not None else "尚无有效样本")
        self._estimate_note.setToolTip(reason)
        remaining = value.get("estimated_remaining_usd")
        self._estimate_period.setText("剩余额度 " + usd(remaining) if remaining is not None else status)

    def _show_estimates(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("周额度估值记录")
        dialog.resize(900, 460)
        apply_theme(dialog, self._theme)
        layout = QVBoxLayout(dialog)
        view = table(["套餐", "额度重置时间", "采样时间段", "整周估值（美元）", "状态"])
        self._estimate_history_table = view
        estimates = self._data.get("weekly_estimates") or []
        view.setRowCount(len(estimates))
        for index, value in enumerate(estimates):
            amount = value.get("estimated_total_usd")
            reset = value.get("reset_at")
            try:
                reset_text = datetime.fromtimestamp(reset).strftime("%Y/%m/%d %H:%M") if isinstance(reset, (int, float)) else "—"
            except (ValueError, OSError, OverflowError):
                reset_text = "—"
            interval, interval_detail = estimate_interval(value)
            status, reason = estimate_status(value)
            columns = (str(value.get("plan_type") or "—").upper(), reset_text, interval,
                       usd(amount) if amount is not None else "—", status)
            for column, text in enumerate(columns):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 2:
                    item.setToolTip(interval_detail)
                elif column == 4:
                    item.setToolTip(reason)
                    item.setForeground(QColor(theme_colors(self._theme)["success" if value.get("status") == "ready" else "muted"]))
                view.setItem(index, column, item)
        header = view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(130)
        header.setStretchLastSection(False)
        layout.addWidget(view)
        layout.addWidget(plain_label("按 API 单价折算，非订阅实际扣款。", muted=True))
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
        self._theme = self._config["theme"]
        self._callback("settings", self._settings)
        self._callback("config", copy.deepcopy(self._config))
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
