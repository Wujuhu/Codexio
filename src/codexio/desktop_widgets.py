"""Small native building blocks for the C desktop interface."""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QByteArray, QDate, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap, QTextCharFormat
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCalendarWidget, QDateEdit, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from codexio.analytics_config import NAVIGATION_PAGES, normalize_navigation_order
from codexio.charts import compact_number, parse_timestamp
from codexio.durations import elapsed_milliseconds, duration_text, duration_tooltip
from codexio.theme import theme_colors
from codexio.money import usd
from codexio.usage_collector import _user_preview

PAGE_TITLES = dict(overview="概览", subscription="订阅额度", trends="用量趋势", logs="请求日志", pricing="模型定价", settings="设置")
NAVIGATION_LABELS = dict(overview="概览", logs="日志", trends="用量", subscription="订阅", pricing="定价", settings="设置")
ICON_PATHS = {
    "overview": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    "subscription": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18M7 15h4m5 0h1"/>',
    "trends": '<path d="M4 3v17h17M8 15l4-6 4 3 5-8"/>',
    "logs": '<path d="M7 5h14M7 12h14M7 19h14M3 5h.1M3 12h.1M3 19h.1"/>',
    "pricing": '<path d="M3 3h8l10 10-8 8L3 11Z"/><circle cx="7.5" cy="7.5" r="1"/>',
    "settings": '<path d="M3 7h18M3 17h18"/><circle cx="9" cy="7" r="3" fill="BACKGROUND"/><circle cx="16" cy="17" r="3" fill="BACKGROUND"/>',
    "refresh": '<path d="M20 8A8 8 0 0 0 6 5L3 8m0-5v5h5M4 16a8 8 0 0 0 14 3l3-3m0 5v-5h-5"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/>',
    "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "edit": '<path d="m14 5 5 5M4 20l5-1L21 7l-5-5L4 14ZM4 20l1-6"/>',
    "widget": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 14h6"/>',
    "increase": '<path d="m3 17 6-6 4 4 8-10M14 5h7v7"/>',
    "decrease": '<path d="m3 7 6 6 4-4 8 10M14 19h7v-7"/>',
    "previous_month": '<path d="m15 5-7 7 7 7"/>',
    "next_month": '<path d="m9 5 7 7-7 7"/>',
}


@lru_cache(maxsize=96)
def ui_icon(name: str, color: str, background: str = "none") -> QIcon:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
           'fill="none" stroke="%s" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">%s</svg>'
           % (color, ICON_PATHS.get(name, ICON_PATHS["overview"]).replace("BACKGROUND", background)))
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class DatePicker(QDateEdit):
    """Consistent date field with an unclipped, six-week native calendar."""
    def __init__(self, value=None, parent=None, *, theme="system"):
        super().__init__(value or QDate.currentDate(), parent)
        self.setObjectName("datePicker")
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(160, 34)
        calendar = QCalendarWidget(self)
        calendar.setObjectName("dateCalendar")
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        calendar.setGridVisible(False)
        calendar.setFixedSize(336, 304)
        self.setCalendarWidget(calendar)
        self.set_theme(theme)

    def set_theme(self, theme):
        c = theme_colors(theme)
        calendar = self.calendarWidget()
        # Calendar cells are not ledger rows. Reset the app's table padding and
        # button sizing so all six weeks fit inside the native popup.
        calendar.setStyleSheet("""
            QCalendarWidget#dateCalendar { background: %(surface)s; color: %(text)s;
                border: 1px solid %(control_border)s; border-radius: 8px; }
            QCalendarWidget#dateCalendar QWidget#qt_calendar_navigationbar { background: %(surface)s; border: none; }
            QCalendarWidget#dateCalendar QToolButton { background: transparent; color: %(text)s;
                border: none; border-radius: 5px; padding: 2px 5px; min-width: 20px; min-height: 28px; font-size: 13px; }
            QCalendarWidget#dateCalendar QToolButton:hover { background: %(hover)s; }
            QCalendarWidget#dateCalendar QSpinBox { background: %(surface)s; color: %(text)s;
                border: 1px solid %(control_border)s; padding: 0px 3px; min-height: 24px; max-height: 28px; }
            QCalendarWidget#dateCalendar QLineEdit { background: transparent; border: none; padding: 0; min-height: 0; }
            QCalendarWidget#dateCalendar QAbstractItemView { background: %(surface)s; color: %(text)s;
                alternate-background-color: %(surface)s; border: none; border-radius: 0; outline: none;
                selection-background-color: %(selection)s; selection-color: %(text)s; font-size: 13px; }
            QCalendarWidget#dateCalendar QAbstractItemView:disabled { color: %(muted)s; }
            QCalendarWidget#dateCalendar QTableView::item { padding: 0; border: none; }
            QCalendarWidget#dateCalendar QTableView::item:selected { background: %(selection)s; color: %(text)s; }
            QCalendarWidget#dateCalendar QTableView::item:hover { background: %(hover)s; }
        """ % c)
        for name, icon in (("qt_calendar_prevmonth", "previous_month"), ("qt_calendar_nextmonth", "next_month")):
            button = calendar.findChild(QToolButton, name)
            if button:
                button.setIcon(ui_icon(icon, c["text"]))
                button.setIconSize(QSize(14, 14))
        weekend = QTextCharFormat()
        weekend.setForeground(QColor(c["comparison_down"]))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            calendar.setWeekdayTextFormat(day, weekend)


class NavigationList(QListWidget):
    page_requested = Signal(str)
    order_changed = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("navigationList")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSpacing(4)
        self.setIconSize(QSize(17, 17))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.itemClicked.connect(lambda item: self.page_requested.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.itemActivated.connect(lambda item: self.page_requested.emit(item.data(Qt.ItemDataRole.UserRole)))
        self.setToolTip("拖动调整页面顺序；概览固定第一。也可右键上移或下移。")
        self.set_order(None)

    def order(self):
        return [self.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.count())]

    def set_order(self, names):
        order = normalize_navigation_order(names)
        if order == self.order():
            return
        selected = self.currentItem().data(Qt.ItemDataRole.UserRole) if self.currentItem() else "overview"
        self.clear()
        for name in order:
            item = QListWidgetItem(NAVIGATION_LABELS[name])
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setSizeHint(QSize(120, 39))
            if name == "overview":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
                item.setToolTip("概览固定第一")
            else:
                item.setToolTip("拖动调整顺序；右键可上移或下移")
            self.addItem(item)
        self.select_page(selected)

    def select_page(self, name):
        for index in range(self.count()):
            if self.item(index).data(Qt.ItemDataRole.UserRole) == name:
                self.setCurrentRow(index)
                break

    def set_theme(self, name):
        colors = theme_colors(name)
        for index in range(self.count()):
            item = self.item(index)
            item.setIcon(ui_icon(item.data(Qt.ItemDataRole.UserRole), colors["text"], colors["sidebar_start"]))

    def move_page(self, name, target):
        if name == "overview" or name not in self.order():
            return False
        order = self.order()
        order.remove(name)
        order.insert(max(1, min(int(target), len(order))), name)
        self.set_order(order)
        self.order_changed.emit(self.order())
        return True

    def startDrag(self, actions):
        if self.currentItem() and self.currentItem().data(Qt.ItemDataRole.UserRole) == "overview":
            return
        super().startDrag(actions)

    def dropEvent(self, event):
        super().dropEvent(event)
        # Qt can insert another page above a non-draggable item. Re-pin by identity.
        current = self.currentItem().data(Qt.ItemDataRole.UserRole) if self.currentItem() else "overview"
        self.set_order(self.order())
        self.select_page(current)
        self.order_changed.emit(self.order())

    def _context_menu(self, position):
        item = self.itemAt(position)
        if item is None:
            return
        name, index = item.data(Qt.ItemDataRole.UserRole), self.row(item)
        menu = QMenu(self)
        up = menu.addAction("上移")
        down = menu.addAction("下移")
        up.setEnabled(name != "overview" and index > 1)
        down.setEnabled(name != "overview" and index < self.count() - 1)
        up.triggered.connect(lambda: self.move_page(name, index - 1))
        down.triggered.connect(lambda: self.move_page(name, index + 1))
        menu.exec(self.viewport().mapToGlobal(position))

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            item = self.currentItem()
            if item:
                self.move_page(item.data(Qt.ItemDataRole.UserRole), self.currentRow() + (-1 if event.key() == Qt.Key.Key_Up else 1))
            event.accept()
            return
        super().keyPressEvent(event)


class SegmentedControl(QWidget):
    changed = Signal(object)

    def __init__(self, items, selected=None, parent=None):
        super().__init__(parent)
        self.setObjectName("segmentedControl")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._value = selected
        for label, value in items:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("segment", True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, key=value: self.set_value(key, notify=True))
            self.group.addButton(button)
            layout.addWidget(button)
            self.buttons[value] = button
        self.set_value(selected if selected in self.buttons else next(iter(self.buttons)))
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def value(self):
        return self._value

    def set_value(self, value, notify=False):
        if value not in self.buttons:
            return
        old = self._value
        self._value = value
        self.buttons[value].setChecked(True)
        if notify and value != old:
            self.changed.emit(value)


class QuotaMeter(QWidget):
    def __init__(self, title, *, gauge=False, parent=None):
        super().__init__(parent)
        self.title, self.gauge = title, gauge
        self.remaining = None
        self.reset_text = "重置时间 —"
        self.note = "等待额度数据"
        self.theme = "system"
        self.setMinimumWidth(150 if not gauge else 175)
        self.setMinimumHeight(90 if not gauge else 238)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName(title)

    def set_value(self, remaining, reset_text, note=""):
        self.remaining = None if remaining is None else max(0, min(100, float(remaining)))
        self.reset_text, self.note = reset_text, note
        self.setAccessibleDescription("剩余 %s；%s；%s" % ("—" if remaining is None else "%g%%" % self.remaining, reset_text, note))
        self.setToolTip(self.accessibleDescription())
        self.update()

    def set_theme(self, name):
        self.theme = name
        self.update()

    def progress_color(self):
        colors = theme_colors(self.theme)
        if self.remaining is None:
            return QColor(colors["raised"])
        lower, upper = (("quota_low", "quota_mid") if self.remaining <= 50 else ("quota_mid", "quota_high"))
        fraction = self.remaining / 50 if self.remaining <= 50 else min(1.0, (self.remaining - 50) / 25)
        first, second = QColor(colors[lower]), QColor(colors[upper])
        return QColor(*(round(a + (b - a) * fraction) for a, b in zip(first.getRgb()[:3], second.getRgb()[:3])))

    def paintEvent(self, event):
        c = theme_colors(self.theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["surface"]))
        p.drawRoundedRect(QRectF(self.rect()), 13, 13)
        font = QFont(self.font())
        font.setPixelSize(13 if self.gauge else 12)
        p.setFont(font)
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(16, 12, self.width() - 32, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        value = "—" if self.remaining is None else "%g%%" % self.remaining
        ink = QColor(c["warning"] if self.remaining is not None and self.remaining <= 15 else c["quota_progress"])
        if self.gauge:
            diameter = min(200, self.width() - 44)
            bounds = QRectF((self.width() - diameter) / 2, 48, diameter, diameter)
            p.setPen(QPen(QColor(c["raised"]), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(bounds, 0, 180 * 16)
            if self.remaining is not None and self.remaining > 0:
                p.setPen(QPen(ink, 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(bounds, 180 * 16, -round(self.remaining / 100 * 180 * 16))
            font.setPixelSize(34)
            font.setWeight(QFont.Weight.Medium)
            p.setFont(font)
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(12, bounds.top() + diameter / 2 - 42, self.width() - 24, 44), Qt.AlignmentFlag.AlignCenter, value)
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.Normal)
            p.setFont(font)
            p.setPen(QColor(c["muted"]))
            p.drawText(QRectF(12, bounds.top() + diameter / 2 + 6, self.width() - 24, 20), Qt.AlignmentFlag.AlignCenter, "剩余")
            y = self.height() - 55
        else:
            font.setPixelSize(14)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            p.setPen(QColor(c["text"]))
            p.drawText(QRectF(80, 12, self.width() - 96, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, value + " 剩余")
            track = QRectF(16, 42, self.width() - 32, 11)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c["raised"]))
            p.drawRoundedRect(track, 5.5, 5.5)
            if self.remaining is not None and self.remaining > 0:
                fill = QRectF(track.left(), track.top(), track.width() * self.remaining / 100, track.height())
                p.setBrush(self.progress_color())
                radius = min(5.5, fill.width() / 2)
                p.drawRoundedRect(fill, radius, radius)
            y = 62
        font.setPixelSize(12 if self.gauge else 11)
        font.setWeight(QFont.Weight.Normal)
        p.setFont(font)
        p.setPen(QColor(c["muted"]))
        fm = QFontMetrics(font)
        p.drawText(QRectF(16, y, self.width() - 32, 20), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(self.reset_text, Qt.TextElideMode.ElideRight, self.width() - 32))
        if self.gauge:
            p.drawText(QRectF(16, y + 23, self.width() - 32, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.note, Qt.TextElideMode.ElideRight, self.width() - 32))
        p.end()


class TokenComposition(QWidget):
    """Each label and percentage is measured as a pair; only groups wrap."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = "system"
        self.parts = [("缓存读取", 0, "token_cache_read"), ("普通输入", 0, "token_input"), ("输出", 0, "token_output")]
        self.setMinimumWidth(150)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setAccessibleName("Token 构成")

    def set_buckets(self, buckets):
        values = {key: sum(max(0, int(row.get(key) or 0)) for row in buckets) for key in ("cache_read", "cache_write", "input", "output")}
        self.parts = [("缓存读取", values["cache_read"], "token_cache_read"), ("普通输入", values["input"], "token_input"), ("输出", values["output"], "token_output")]
        if values["cache_write"]:
            self.parts.insert(1, ("缓存写入", values["cache_write"], "token_cache_write"))
        self.setToolTip("\n".join("%s：%s Token" % (label, format(value, ",")) for label, value, _ in self.parts))
        self.setAccessibleDescription(self.toolTip())
        self._fit_height()
        self.update()

    def set_theme(self, name):
        self.theme = name
        self.update()

    def legend_geometry(self, width=None):
        width = self.width() if width is None else width
        font = QFont(self.font())
        font.setPixelSize(12)
        fm = QFontMetrics(font)
        total = sum(value for _, value, _ in self.parts)
        x, y, result = 16, 67, []
        for label, value, color in self.parts:
            ratio = ("%.1f" % (value / total * 100)).rstrip("0").rstrip(".") + "%" if total else "—"
            label_width, value_width = fm.horizontalAdvance(label), fm.horizontalAdvance(ratio)
            group_width = 14 + label_width + 6 + value_width
            if x > 16 and x + group_width > width - 16:
                x, y = 16, y + fm.height() + 8
            result.append((label, ratio, color, QRectF(x + 14, y, label_width, fm.height()),
                           QRectF(x + 14 + label_width + 6, y, value_width, fm.height())))
            x += group_width + 20
        return result

    def _fit_height(self):
        rows = self.legend_geometry()
        height = max(90, math.ceil(max((r[4].bottom() for r in rows), default=74) + 14))
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)

    def segment_geometry(self):
        positive = [(label, value, color) for label, value, color in self.parts if value > 0]
        total = sum(value for _, value, _ in positive)
        if not total:
            return []
        width = max(0, self.width() - 32 - 4 * (len(positive) - 1))
        left, result = 16.0, []
        for label, value, color in positive:
            segment = width * value / total
            result.append((label, color, QRectF(left, 42, segment, 11)))
            left += segment + 4
        return result

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme_colors(self.theme)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["surface"]))
        p.drawRoundedRect(QRectF(self.rect()), 13, 13)
        font = QFont(self.font())
        font.setPixelSize(12)
        p.setFont(font)
        p.setPen(QColor(c["muted"]))
        p.drawText(QRectF(16, 12, self.width() - 32, 20), Qt.AlignmentFlag.AlignVCenter, "Token 构成")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c["raised"]))
        segments = self.segment_geometry()
        if not segments:
            p.drawRoundedRect(QRectF(16, 42, self.width() - 32, 11), 5.5, 5.5)
        for _, color, rect in segments:
            p.setBrush(QColor(c[color]))
            radius = min(5.5, rect.width() / 2)
            p.drawRoundedRect(rect, radius, radius)
        font.setPixelSize(12)
        p.setFont(font)
        for label, ratio, color, label_rect, value_rect in self.legend_geometry():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c[color]))
            p.drawEllipse(QPointF(label_rect.left() - 10, label_rect.center().y()), 4, 4)
            p.setPen(QColor(c["muted"]))
            p.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter, label)
            p.setPen(QColor(c["text"]))
            p.drawText(value_rect, Qt.AlignmentFlag.AlignVCenter, ratio)
        p.end()


class PeriodChange(QWidget):
    """A percent change with explicit direction and the comparison period."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.marker = QLabel()
        self.marker.setFixedSize(16, 18)
        self.value = QLabel()
        self.value.setTextFormat(Qt.TextFormat.PlainText)
        self.caption = QLabel()
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setWordWrap(True)
        layout.addWidget(self.marker)
        layout.addWidget(self.value)
        layout.addWidget(self.caption, 1)
        self._comparison, self._metric, self._theme = None, "tokens", "system"
        self.hide()

    def set_theme(self, theme):
        self.set_comparison(self._comparison, self._metric, theme)

    def set_comparison(self, data, metric, theme):
        self._comparison, self._metric, self._theme = data, metric, theme
        self.setVisible(data is not None)
        if data is None:
            return
        c = theme_colors(theme)
        change = data["changes"][metric]
        percent = change["percent"]
        self.marker.hide()
        color = c["muted"]
        if percent is not None:
            direction = "increase" if percent > 0 else "decrease" if percent < 0 else None
            color = c["comparison_up" if percent > 0 else "comparison_down"] if direction else c["muted"]
            tiny = 0 < abs(percent) < .05
            amount = "<0.1%" if tiny else ("+" if percent > 0 else "−" if percent < 0 else "") + "%.1f%%" % abs(percent)
            self.value.setText(amount)
            if direction:
                self.marker.setPixmap(ui_icon(direction, color).pixmap(16, 16))
                self.marker.show()
        else:
            self.value.setText("前期为 0" if change["status"] == "zero_baseline" else "暂无对比")
        self.value.setStyleSheet("color: %s; font-size: 12px; font-weight: 600;" % color)
        self.caption.setStyleSheet("color: %s; font-size: 12px;" % c["muted"])
        self.caption.setText(data["label"])
        def amount(value):
            return "暂无有效数据" if value is None else usd(value) if metric == "usd" else format(int(value), ",")
        def when(value):
            stamp = parse_timestamp(value)
            return stamp.strftime("%Y/%m/%d %H:%M") if stamp else "—"
        tooltip = "本期 %s — %s：%s\n前期 %s — %s：%s" % (
            when(data["start"]), when(data["end"]), amount(data["current"][metric]),
            when(data["previous_start"]), when(data["previous_end"]), amount(data["previous"][metric]))
        if any(data[period].get("skipped", {}).get(metric) for period in ("current", "previous")):
            tooltip += "\n按已确认数据计算"
        self.setToolTip(tooltip)
        direction_text = "增加 " if percent is not None and percent > 0 else "减少 " if percent is not None and percent < 0 else ""
        self.setAccessibleName(direction_text + self.value.text() + " · " + self.caption.text())


class WidgetStylePreview(QWidget):
    """Reuse the floating window's actual content, with local draft appearance."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._style_name = None
        self._content = None
        self._settings = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self.setAccessibleName("悬浮窗样式预览")

    def configure(self, settings, state):
        from codexio.rate_limits import QuotaState, should_show_five_hour
        from codexio.theme import apply_theme
        from codexio.visuals import create_style_widget
        self._settings = settings
        if self._style_name != settings.visual_style:
            if self._content:
                self._layout.removeWidget(self._content)
                self._content.hide()
                self._content.deleteLater()
            self._content = create_style_widget(settings.visual_style, self)
            self._style_name = settings.visual_style
            self._layout.addWidget(self._content)
            size = self._content.preferred_size()
            self.setFixedSize(min(360, size.width() + 32), min(260, size.height() + 32))
            apply_theme(self, "dark")
        actual = state if isinstance(state, QuotaState) else QuotaState.empty()
        self._content.set_show_five(should_show_five_hour(settings.quota_scope, actual))
        self._content.apply(actual)
        if not self.isVisible():
            self._settle_animations()
        self.update()

    def _settle_animations(self):
        from codexio.visuals import PercentAnimator
        for animator in self.findChildren(PercentAnimator):
            animator.finish()

    def hideEvent(self, event):
        self._settle_animations()
        super().hideEvent(event)

    def paintEvent(self, event):
        if self._settings is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(theme_colors("dark")["surface"])
        color.setAlpha(self._settings.background_alpha())
        p.setBrush(color)
        p.setPen(QPen(QColor(self._settings.border_color), 1) if self._settings.show_border else Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 18, 18)
        p.end()


def preview_title(record):
    text = " ".join(_user_preview(record.get("prompt_preview") or "").split())
    if text:
        return text
    title = str(record.get("session_title") or "").strip()
    if title:
        return "会话：" + title
    session = str(record.get("session_id") or "")
    if session:
        return "Session " + (session[:8] + "…" + session[-6:] if len(session) > 18 else session)
    return "调用 " + str(record.get("id") or "未记录")[-12:]


def tier_label(record):
    return {"priority": "Fast", "fast": "Fast", "default": "Standard", "standard": "Standard", "mixed": "Mixed"}.get(str(record.get("service_tier") or "").lower(), "未记录")


def ledger_duration_text(record, now=None):
    value = elapsed_milliseconds(record, now)
    if value is None:
        return "—"
    if value < 1000:
        return "%.2f 秒" % (value / 1000)
    seconds = int(value / 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return ("%d:%02d:%02d" % (hours, minutes, seconds)) if hours else ("%02d:%02d" % (minutes, seconds))


SECONDARY_ROLE = int(Qt.ItemDataRole.UserRole) + 1
PRIMARY_COLOR_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class LedgerDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        lines = str(index.data(Qt.ItemDataRole.DisplayRole) or "").split("\n", 1)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setClipRect(option.rect)
        rect = QRectF(option.rect).adjusted(9, 4, -9, -4)
        font = QFont(opt.font)
        font.setPixelSize(13 if index.data(PRIMARY_COLOR_ROLE) == "running" else 12)
        fm = QFontMetrics(font)
        small = QFont(font)
        small.setPixelSize(11)
        sm = QFontMetrics(small)
        total = fm.height() + (sm.height() + 5 if len(lines) > 1 else 0)
        y = rect.center().y() - total / 2
        colors = theme_colors(self.parent()._theme)
        color = colors.get(index.data(PRIMARY_COLOR_ROLE), colors["text"])
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRectF(rect.left(), y, rect.width(), fm.height()), Qt.AlignmentFlag.AlignCenter,
                         fm.elidedText(lines[0], Qt.TextElideMode.ElideRight, int(rect.width())))
        if len(lines) > 1:
            painter.setFont(small)
            painter.setPen(QColor(colors["muted"]))
            painter.drawText(QRectF(rect.left(), y + fm.height() + 5, rect.width(), sm.height()), Qt.AlignmentFlag.AlignCenter,
                             sm.elidedText(lines[1], Qt.TextElideMode.ElideRight, int(rect.width())))
        painter.restore()


class LedgerTable(QTableWidget):
    duration_column = 5

    def __init__(self, grouped=True, compact=False, parent=None):
        super().__init__(parent)
        self.setObjectName("requestLedger")
        self._theme = "system"
        self.grouped, self.compact = grouped, compact
        self._show_source = False
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(63 if not compact else 58)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.setItemDelegate(LedgerDelegate(self))
        self._headers = []
        self.set_mode(grouped)
        self.setMinimumWidth(0)

    def set_mode(self, grouped):
        self.grouped = grouped
        if self.compact:
            headers = ["用户请求 / 发起时间", "模型", "档位", "API 等价", "状态"]
            self.weights = [42, 22, 10, 16, 10]
        else:
            headers = ["用户请求 / 发起时间" if grouped else "关联输入 / 计量时间", "模型", "档位", "输入 / 输出", "API 等价", "耗时"]
            headers += ["状态", "来源"] if grouped else ["来源"]
            self.weights = [25, 15, 8, 14, 14, 9, 7, 8] if grouped else [27, 16, 9, 15, 15, 10, 8]
        changed = self.set_headers(headers)
        self._apply_source_visibility()
        return changed

    def set_source_visible(self, visible):
        self._show_source = bool(visible)
        self._apply_source_visibility()

    def _apply_source_visibility(self):
        for column in range(self.columnCount()):
            self.setColumnHidden(column, not self.compact and not self._show_source and column == self.columnCount() - 1)
        self.fit_columns()

    def set_headers(self, headers):
        if headers == self._headers:
            return False
        self._headers = list(headers)
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)
        for index in range(len(headers)):
            self.horizontalHeaderItem(index).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fit_columns()
        return True

    def queue_columns(self):
        self.fit_columns()

    def set_theme(self, theme):
        self._theme = theme
        self.viewport().update()

    def fit_columns(self):
        if not getattr(self, "weights", None):
            return
        minimum = 650 if self.compact else 760
        font = QFont(self.font())
        font.setPixelSize(12)
        fm = QFontMetrics(font)
        base = [200, 125, 78, 110, 58] if self.compact else ([178, 110, 72, 88, 105, 68, 64, 72] if self.grouped else [198, 120, 72, 95, 110, 75, 78])
        for column in ([3] if self.compact else [3, 4, 5]):
            for row in range(self.rowCount()):
                item = self.item(row, column)
                if item:
                    base[column] = max(base[column], max(fm.horizontalAdvance(line) for line in item.text().splitlines()) + 20)
        visible = [column for column in range(len(self.weights)) if not self.isColumnHidden(column)]
        if not visible:
            return
        minimum -= sum(base[column] for column in range(len(base)) if column not in visible)
        available = max(minimum, self.viewport().width(), sum(base[column] for column in visible))
        surplus = available - sum(base[column] for column in visible)
        weight_total = sum(self.weights[column] for column in visible)
        used = 0
        for index, column in enumerate(visible):
            width = base[column] + round(surplus * self.weights[column] / weight_total) if index < len(visible) - 1 else available - used
            self.setColumnWidth(column, width)
            used += width

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_columns()

    def set_records(self, rows, cost_formatter, theme="system"):
        self.set_theme(theme)
        self.setUpdatesEnabled(False)
        self.setRowCount(len(rows))
        c = theme_colors(theme)
        for index, row in enumerate(rows):
            stamp = parse_timestamp(row.get("timestamp"))
            date = stamp.strftime("%m/%d %H:%M:%S") if stamp else "—"
            identity = (("未归属调用" if row.get("record_kind") == "unassigned" else "%s 次调用" % row.get("call_count", 0))
                        if self.grouped else ("ID " + str(row.get("id") or "")[-8:]))
            model = row.get("model") or "未知模型"
            models = row.get("models") or []
            if len(models) > 1:
                model += "\n" + " / ".join(models)
            values = [preview_title(row) + "\n" + date + " · " + identity, model, tier_label(row)]
            if not self.compact:
                values.append(compact_number(row.get("input_tokens")) + "\n" + compact_number(row.get("output_tokens")))
            values.append(cost_formatter(row))
            if not self.compact:
                values.append(ledger_duration_text(row))
            if self.grouped:
                values.append({"running": "回复中", "completed": "完成", "aborted": "已中断"}.get(row.get("request_status"), "未知"))
            if not self.compact:
                values.append(row.get("source_name") or row.get("source_id") or "—")
            for column, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(str(text))
                self.setItem(index, column, item)
            # Full request text is available in the persistent details pane.
            self.item(index, 0).setToolTip("")
            self.item(index, 1).setToolTip("\n".join(models or [row.get("model") or "未知模型"]))
            self.item(index, 2).setToolTip("Mixed：整轮包含不同档位的调用；未记录：不推断为 Standard。")
            if not self.compact:
                self.item(index, self.duration_column).setToolTip(duration_tooltip(row))
            if self.grouped and row.get("request_status") == "running":
                status = self.item(index, 4 if self.compact else 6)
                status.setData(PRIMARY_COLOR_ROLE, "running")
                emphasis = QFont(self.font())
                emphasis.setBold(True)
                status.setFont(emphasis)
        self.setUpdatesEnabled(True)
        self.fit_columns()


class DrawerHost(QWidget):
    """Permanent side-by-side request table and details; never an overlay."""

    def __init__(self, primary, inspector, parent=None):
        super().__init__(parent)
        self.primary, self.inspector = primary, inspector
        primary.setParent(self)
        inspector.setParent(self)
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _place(self):
        width, height = self.width(), self.height()
        detail = min(320, max(260, round(width * .30)))
        inset = detail + 20
        self.primary.setGeometry(0, 0, max(0, width - inset), height)
        self.inspector.setGeometry(max(0, width - detail), 0, min(detail, width), height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()
