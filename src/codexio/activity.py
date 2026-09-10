"""A bounded, local-calendar view of recorded daily Token usage."""
from __future__ import annotations

import bisect
import math
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from codexio.charts import ChartTooltip, compact_number, parse_timestamp
from codexio.theme import theme_colors
from codexio.money import usd


def activity_bounds(now=None):
    current = (now or datetime.now().astimezone()).astimezone()
    start = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=364)
    return start, current


class UsageActivity(QWidget):
    day_clicked = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = "system"
        self._tooltip = ChartTooltip(self)
        self._hover = None
        self._focused = date.today()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("每日 Token 活动；方向键选择日期，回车查看日志")
        self.setMinimumWidth(570)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_buckets([])

    def set_theme(self, theme):
        self._theme = theme
        self._tooltip.hide()
        self.update()

    def set_buckets(self, buckets, now=None):
        start, end = activity_bounds(now)
        self.start, self.end = start.date(), end.date()
        self._grid_start = self.start - timedelta(days=self.start.weekday())
        self.days = {}
        for bucket in buckets:
            stamp = bucket.get("timestamp")
            stamp = stamp.astimezone() if isinstance(stamp, datetime) else parse_timestamp(stamp)
            if stamp is not None and self.start <= stamp.date() <= self.end:
                self.days[stamp.date()] = dict(bucket, timestamp=stamp)
        positive = sorted(int(row.get("tokens") or 0) for row in self.days.values() if (row.get("tokens") or 0) > 0)
        self._thresholds = []
        for quarter in (1, 2, 3):
            if positive:
                index, fraction = divmod((len(positive) - 1) * quarter, 4)
                self._thresholds.append(positive[index] * (4 - fraction) + positive[min(index + 1, len(positive) - 1)] * fraction)
        self._hover = None
        self._focused = max(self.start, min(self.end, self._focused))
        self._tooltip.hide()
        self.setAccessibleDescription(self.summary_text() + "；" + self.range_text())
        self._fit_height()
        self.update()

    def range_text(self):
        return self.start.strftime("%Y/%m/%d") + " – " + self.end.strftime("%Y/%m/%d")

    def summary_text(self):
        occupied = [row for row in self.days.values() if row.get("requests", 0) or row.get("tokens", 0)]
        if not occupied:
            return "近一年暂无活动记录"
        known = [row["tokens"] for row in occupied if row.get("tokens") is not None]
        amount = compact_number(sum(known)) + " Token" if known else "Token 暂无有效数据"
        return "%s 天有记录 · %s" % (len(occupied), amount)

    def level(self, day):
        amount = int(self.days.get(day, {}).get("tokens") or 0)
        return min(4, 1 + bisect.bisect_right(self._thresholds, amount * 4)) if amount > 0 else 0

    def cell_geometry(self):
        weeks = (self.end - self._grid_start).days // 7 + 1
        gap = 4 if self.width() >= 900 else 3 if self.width() >= 730 else 2
        size = min(16.0, (self.width() - 34) / weeks - gap)
        step = size + gap
        return [(self._grid_start + timedelta(days=index),
                 QRectF(28 + (index // 7) * step, 26 + (index % 7) * step, size, size))
                for index in range((self.end - self._grid_start).days + 1)
                if self.start <= self._grid_start + timedelta(days=index) <= self.end]

    def _fit_height(self):
        geometry = self.cell_geometry()
        height = max(174, math.ceil(max(rect.bottom() for _, rect in geometry) + 40))
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def sizeHint(self):
        return QSize(850, 200)

    def paintEvent(self, event):
        colors = theme_colors(self._theme)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPixelSize(11)
        painter.setFont(font)
        geometry = self.cell_geometry()
        months = set()
        for day, rect in geometry:
            month = (day.year, day.month)
            if month not in months and (day == self.start or day.day == 1):
                painter.setPen(QColor(colors["muted"]))
                painter.drawText(QRectF(rect.left(), 0, 36, 19), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "%d月" % day.month)
                months.add(month)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["activity_%d" % self.level(day)] if self.level(day) else colors["activity_empty"]))
            painter.drawRoundedRect(rect, 2, 2)
            if day == self._hover or self.hasFocus() and day == self._focused:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(colors["text"]), 1.4))
                painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 2, 2)
        painter.setPen(QColor(colors["muted"]))
        for weekday, label in ((0, "一"), (2, "三"), (4, "五")):
            rect = next((rect for day, rect in geometry if day.weekday() == weekday), None)
            if rect is not None:
                painter.drawText(QRectF(0, rect.top() - 2, 20, rect.height() + 4), Qt.AlignmentFlag.AlignVCenter, label)
        baseline = self.height() - 22
        painter.drawText(QRectF(0, baseline, self.width() - 160, 20), Qt.AlignmentFlag.AlignVCenter, "点击方格查看当天日志")
        left = self.width() - 144
        painter.drawText(QRectF(left, baseline, 20, 20), Qt.AlignmentFlag.AlignVCenter, "少")
        for level in range(5):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["activity_%d" % level] if level else colors["activity_empty"]))
            painter.drawRoundedRect(QRectF(left + 24 + level * 17, baseline + 4, 12, 12), 2, 2)
        painter.setPen(QColor(colors["muted"]))
        painter.drawText(QRectF(left + 113, baseline, 24, 20), Qt.AlignmentFlag.AlignVCenter, "多")
        painter.end()

    def _day_at(self, position):
        return next((day for day, rect in self.cell_geometry() if rect.contains(position)), None)

    def tooltip_text(self, day):
        lines = [day.strftime("%Y/%m/%d") + " · 周" + "一二三四五六日"[day.weekday()]]
        row = self.days.get(day, {})
        if not row.get("requests") and not row.get("tokens"):
            return "\n".join(lines + ["暂无记录"])
        lines.extend(["Total Token  暂无有效数据" if row.get("tokens") is None else "Total Token  {:,}".format(row["tokens"]),
                      "请求数  {:,}".format(row.get("requests", 0)),
                      "价格  " + usd(row.get("usd"))])
        if row.get("unpriced"):
            lines.append("%d 次请求未定价" % row["unpriced"])
        return "\n".join(lines)

    def mouseMoveEvent(self, event):
        self._hover = self._day_at(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._hover else Qt.CursorShape.ArrowCursor)
        if self._hover:
            self._tooltip.show_at(event.globalPosition().toPoint(), self.tooltip_text(self._hover), self._theme)
        else:
            self._tooltip.hide()
        self.update()

    def _activate(self, day):
        self._tooltip.hide()
        stamp = datetime.combine(day, time.min).astimezone()
        self.day_clicked.emit(dict(self.days.get(day, {}), timestamp=stamp))

    def mousePressEvent(self, event):
        day = self._day_at(event.position())
        if day is not None and event.button() == Qt.MouseButton.LeftButton:
            self._focused = day
            self._activate(day)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        movement = {Qt.Key.Key_Left: -7, Qt.Key.Key_Right: 7, Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}
        if event.key() in movement:
            self._focused = max(self.start, min(self.end, self._focused + timedelta(days=movement[event.key()])))
            self.update()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._activate(self._focused)
        else:
            super().keyPressEvent(event)

    def leaveEvent(self, event):
        self._hover = None
        self._tooltip.hide()
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._tooltip.hide()
        super().hideEvent(event)
