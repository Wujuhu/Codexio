"""Native, interactive token/cost chart and local-time aggregation helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetrics, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from aiquota.theme import theme_colors


def parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except (ValueError, OverflowError, OSError):
        return None


def period_bounds(period: str, now: Optional[datetime] = None) -> tuple[Optional[datetime], datetime]:
    current = (now or datetime.now().astimezone()).astimezone()
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    days = {"today": 1, "week": 7, "month": 30, "7days": 7, "30days": 30}
    return (midnight - timedelta(days=days[period] - 1) if period in days else None, current)


def compact_number(value: object) -> str:
    try:
        n = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= limit:
            return ("%.2f" % (n / limit)).rstrip("0").rstrip(".") + suffix
    return "%s" % int(n)


def bucket_records(records: list[dict], period: str = "today", granularity: str = "hour",
                   now: Optional[datetime] = None, start: Optional[datetime] = None,
                   end: Optional[datetime] = None) -> list[dict]:
    """Cost stays nullable: unpriced requests do not silently become free requests."""
    default_start, default_end = period_bounds(period, now)
    lower, upper = start or default_start, end or default_end
    values: dict[datetime, dict] = {}
    delta = timedelta(hours=1) if granularity == "hour" else timedelta(days=7 if granularity == "week" else 1)

    def floor(stamp: datetime) -> datetime:
        point = stamp.replace(minute=0, second=0, microsecond=0)
        if granularity != "hour":
            point = point.replace(hour=0)
        if granularity == "week":
            point -= timedelta(days=point.weekday())
        return point

    def empty(stamp: datetime) -> dict:
        return dict(timestamp=stamp, input=0, cache_read=0, cache_write=0, output=0,
                    tokens=0, usd=0.0, requests=0, unpriced=0)

    for row in records:
        stamp = parse_timestamp(row.get("timestamp"))
        if stamp is None or stamp > upper or (lower is not None and stamp < lower):
            continue
        b = values.setdefault(floor(stamp), empty(floor(stamp)))
        inp = max(0, int(row.get("input_tokens") or 0))
        cached = max(0, int(row.get("cached_input_tokens") or 0))
        written = max(0, int(row.get("cache_write_input_tokens") or 0))
        out = max(0, int(row.get("output_tokens") or 0))
        b["input"] += max(0, inp - cached - written)
        b["cache_read"] += cached
        b["cache_write"] += written
        b["output"] += out
        b["tokens"] += int(row.get("total_tokens") or (inp + out))
        b["requests"] += 1
        cost = row.get("cost_usd")
        if cost is None:
            b["unpriced"] += 1
        else:
            b["usd"] += float(cost)
    if not values:
        return []
    for bucket in values.values():
        if bucket["requests"] and bucket["unpriced"] == bucket["requests"]:
            bucket["usd"] = None
    first = floor(lower) if lower else min(values)
    last = floor(upper)
    # The widget only paints occupied buckets for very large hourly histories.
    if (last - first) / delta > 2000:
        return [values[k] for k in sorted(values)]
    while first <= last:
        values.setdefault(first, empty(first))
        first += delta
    return [values[k] for k in sorted(values)]


class ChartTooltip(QFrame):
    """Color-coded metric rows with a separate timestamp heading."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip)
        self.setObjectName("chartTooltip")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedWidth(292)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        self.title_label = QLabel(self)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title_label)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(8)
        layout.addLayout(self.grid)
        self.rows = []
        self.plain_text = ""
        self._content_key = None

    def show_at(self, position: QPoint, text: str, theme: str) -> None:
        colors = theme_colors(theme)
        key = (text, theme, colors["surface"], colors["text"])
        if key != self._content_key:
            self._content_key = key
            self._set_content(text, colors)
        point = position + QPoint(14, 18)
        screen = QApplication.screenAt(position) or self.screen()
        if screen is not None:
            bounds = screen.availableGeometry()
            point.setX(max(bounds.left(), min(point.x(), bounds.right() - self.width() + 1)))
            point.setY(max(bounds.top(), min(point.y(), bounds.bottom() - self.height() + 1)))
        self.move(point)
        self.show()

    def _set_content(self, text: str, colors: dict) -> None:
        self.setStyleSheet("QFrame#chartTooltip { background: %(surface)s; border: 1px solid %(border)s; border-radius: 10px; }"
                          "QFrame#chartTooltip QLabel { font-size: 16px; background: transparent; border: none; }" % colors)
        self.plain_text = text
        self.setAccessibleDescription(text)
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.rows = []
        entries = text.split("\n")
        known = {"Total Token": "chart_tokens", "价格": "chart_cost", "普通输入": "chart_input",
                 "缓存创建": "chart_cache_write", "缓存利用": "chart_cache_read", "输出": "chart_output",
                 "请求数": "muted"}
        heading = entries.pop(0) if entries and entries[0].partition("  ")[0] not in known else ""
        self.title_label.setText(heading)
        self.title_label.setStyleSheet("color: %s; font-weight: 600;" % colors["text"])
        self.title_label.setVisible(bool(heading))
        font = self.font()
        font.setPixelSize(16)
        metrics = QFontMetrics(font)
        label_width = max([metrics.horizontalAdvance(line.partition("  ")[0] + "：") for line in entries if "  " in line] or [80]) + 2
        label_width = min(112, label_width)
        value_width = max(100, 264 - 8 - 12 - label_width)
        for index, line in enumerate(entries):
            name, separator, value = line.partition("  ")
            color = colors.get(known.get(name, "warning"), colors["text"])
            marker = QLabel("●")
            marker.setFixedSize(8, metrics.height())
            marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
            marker.setStyleSheet("color: %s; font-size: 10px;" % color)
            self.grid.addWidget(marker, index, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel((name + "：") if separator else "")
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setFont(font)
            label.setStyleSheet("color: %s; font-weight: 600;" % color)
            label.setFixedWidth(label_width)
            amount = QLabel(value if separator else line)
            amount.setTextFormat(Qt.TextFormat.PlainText)
            amount.setFont(font)
            amount.setWordWrap(True)
            amount.setStyleSheet("color: %s;" % color)
            if separator:
                self.grid.addWidget(label, index, 1, Qt.AlignmentFlag.AlignTop)
                amount.setFixedWidth(value_width)
                self.grid.addWidget(amount, index, 2, Qt.AlignmentFlag.AlignTop)
            else:
                label.deleteLater()
                amount.setFixedWidth(264 - 14)
                self.grid.addWidget(amount, index, 1, 1, 2)
            amount.setMinimumHeight(amount.heightForWidth(amount.width()))
            self.rows.append({"name": name, "value": value if separator else line, "color": color,
                              "marker": marker, "label": label if separator else None, "value_label": amount})
        self.adjustSize()


@dataclass
class _ChartGeometry:
    plot: QRectF
    token_title: QRectF
    cost_title: QRectF
    token_ticks: list[tuple[QRectF, str]]
    cost_ticks: list[tuple[QRectF, str]]
    time_ticks: list[tuple[QRectF, str, int]]
    legend: list[tuple[QRectF, str]]


def _axis_label(value: float, currency: bool = False) -> str:
    """Keep large axis values readable; the hover preview retains full precision."""
    if currency and abs(value) < 1000:
        return "$%.2f" % value
    for limit, suffix in ((1e18, "E"), (1e15, "P"), (1e12, "T")):
        if abs(value) >= limit:
            text = ("%.2f" % (value / limit)).rstrip("0").rstrip(".") + suffix
            break
    else:
        text = compact_number(value)
    return ("$" if currency else "") + text


class UsageChart(QWidget):
    SERIES = (("usd", "价格", "#F1788F"), ("tokens", "Total Token", "#9B81F5"),
              ("cache_write", "缓存创建", "#F3AF61"),
              ("cache_read", "缓存利用", "#BBA8FA"), ("input", "普通输入", "#6699F5"),
              ("output", "输出", "#54C6A1"))

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(310)
        self.setMinimumWidth(440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAccessibleName("Total Token 与价格使用趋势，可点击底部图例切换曲线")
        self.buckets: list[dict] = []
        self._theme = "system"
        self._enabled = {"usd", "tokens"}
        self._legend: list[tuple[QRectF, str]] = []
        self._plot = QRectF()
        self._hover = -1
        self._granularity = "hour"
        self._tooltip = ChartTooltip(self)

    def set_theme(self, name: str) -> None:
        self._theme = name
        self.update()

    def set_records(self, records: list[dict], period: str = "today", granularity: str = "hour",
                    start: Optional[datetime] = None, end: Optional[datetime] = None) -> None:
        self.set_buckets(bucket_records(records, period, granularity, start=start, end=end), granularity)

    def set_buckets(self, buckets: list[dict], granularity: str = "hour") -> None:
        """Accept database aggregates without retaining individual calls."""
        self._granularity = granularity
        self.buckets = buckets
        self._hover = -1
        self._tooltip.hide()
        self.update()

    def _update_minimum_chart_height(self) -> None:
        metrics = self.fontMetrics()
        text_height = metrics.height() + 2
        text_gap = max(10, math.ceil(text_height * 0.3))
        legend_height = self.height() - min(rect.top() for rect, _ in self._layout_legend(metrics))
        required = math.ceil(text_height * 3 + text_gap * 3 + legend_height + max(120, metrics.height() * 3))
        if self.minimumHeight() != max(310, required):
            self.setMinimumHeight(max(310, required))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_minimum_chart_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_minimum_chart_height()

    def _layout_legend(self, metrics) -> list[tuple[QRectF, str]]:
        item_width = max(metrics.horizontalAdvance(label) + 38 for _, label, _ in self.SERIES)
        row_height = max(29, metrics.height() + 10)
        columns = min(len(self.SERIES), max(1, int((self.width() - 20) // item_width)))
        rows = math.ceil(len(self.SERIES) / columns)
        columns = math.ceil(len(self.SERIES) / rows)
        rects = []
        for row in range(rows):
            items = self.SERIES[row * columns:(row + 1) * columns]
            left = (self.width() - len(items) * item_width) / 2
            top = self.height() - 6 - (rows - row) * row_height
            for column, (key, _label, _color) in enumerate(items):
                rects.append((QRectF(left + column * item_width, top, item_width, row_height - 2), key))
        return rects

    def _chart_geometry(self, metrics, token_max: float, usd_max: float) -> _ChartGeometry:
        """Use the actual paint font for every label, including scaled Windows fonts."""
        text_height = metrics.height() + 2
        text_gap = max(10, math.ceil(text_height * 0.3))
        legend = self._layout_legend(metrics)
        legend_top = min(rect.top() for rect, _ in legend)
        top = text_height * 1.5 + text_gap
        bottom = legend_top - text_height * 1.5 - text_gap * 2
        # A narrow chart can wrap the legend onto three rows at larger font sizes.
        # Reduce ticks to preserve a full line of vertical space between values.
        tick_count = max(2, min(5, int((bottom - top) / (text_height + 8)) + 1))
        token_labels = [_axis_label(token_max * tick / (tick_count - 1)) for tick in range(tick_count)]
        cost_labels = [_axis_label(usd_max * tick / (tick_count - 1), True) for tick in range(tick_count)]

        def text_width(text: str) -> float:
            return max(metrics.horizontalAdvance(text), metrics.boundingRect(text).width()) + 4

        token_width = max(map(text_width, token_labels))
        cost_width = max(map(text_width, cost_labels))
        plot = QRectF(token_width + text_gap, top,
                      max(1, self.width() - token_width - cost_width - text_gap * 2),
                      max(1, bottom - top))
        token_ticks, cost_ticks = [], []
        for tick, (token_label, cost_label) in enumerate(zip(token_labels, cost_labels)):
            y = plot.bottom() - plot.height() * tick / (tick_count - 1)
            token_ticks.append((QRectF(0, y - text_height / 2, token_width, text_height), token_label))
            cost_ticks.append((QRectF(plot.right() + text_gap, y - text_height / 2,
                                     cost_width, text_height), cost_label))

        time_ticks = []
        if self.buckets:
            labels = [bucket["timestamp"].strftime("%m/%d %H:%M" if self._granularity == "hour" else "%m/%d")
                      for bucket in self.buckets]
            label_width = max(map(text_width, labels))
            count = len(labels)
            steps = min(6, count, max(1, int(plot.width() // (label_width + text_gap)) + 1))
            while steps:
                indices = ([count // 2] if steps == 1 else
                           [round(step * (count - 1) / (steps - 1)) for step in range(steps)])
                time_ticks = []
                for index in indices:
                    x = plot.left() + plot.width() * index / max(1, count - 1)
                    left = max(0, min(x - label_width / 2, self.width() - label_width))
                    time_ticks.append((QRectF(left, plot.bottom() + text_height / 2 + text_gap,
                                              label_width, text_height), labels[index], index))
                # Endpoint labels move inward to stay on screen, which can bring
                # them closer to their neighbor than the sampled point spacing.
                if all(right[0].left() - left[0].right() >= text_gap
                       for left, right in zip(time_ticks, time_ticks[1:])):
                    break
                steps -= 1
        return _ChartGeometry(
            plot, QRectF(0, 0, text_width("Total Token"), text_height),
            QRectF(self.width() - text_width("价格"), 0, text_width("价格"), text_height),
            token_ticks, cost_ticks, time_ticks, legend)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme_colors(self._theme)
        token_max = max((b[k] for b in self.buckets for k in self._enabled if k != "usd"), default=0)
        usd_max = max((b["usd"] for b in self.buckets if b["usd"] is not None), default=0)
        token_max = max(1, token_max * 1.12)
        usd_max = max(0.01, usd_max * 1.12)
        geometry = self._chart_geometry(painter.fontMetrics(), token_max, usd_max)
        self._legend, self._plot = geometry.legend, geometry.plot
        plot = self._plot
        text_pen = QPen(QColor(c.get("chart_text", c["muted"])))
        painter.setPen(text_pen)
        painter.drawText(geometry.token_title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Total Token")
        painter.drawText(geometry.cost_title, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "价格")
        for (token_rect, token_label), (cost_rect, cost_label) in zip(geometry.token_ticks, geometry.cost_ticks):
            y = token_rect.center().y()
            painter.setPen(QPen(QColor(c["grid"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text_pen)
            painter.drawText(token_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, token_label)
            painter.drawText(cost_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, cost_label)
        if not self.buckets:
            painter.setPen(text_pen)
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             "当前时间范围\n暂无请求记录")
        else:
            count = len(self.buckets)

            def xpos(index: int) -> float:
                return plot.left() + plot.width() * index / max(1, count - 1)

            for key, _label, color in reversed(self.SERIES):
                if key not in self._enabled:
                    continue
                color = c.get("chart_cost" if key == "usd" else "chart_" + key, color)
                maximum = usd_max if key == "usd" else token_max
                path = QPainterPath()
                connected = False
                for index, bucket in enumerate(self.buckets):
                    if bucket[key] is None:
                        connected = False
                        continue
                    point = QPointF(xpos(index), plot.bottom() - plot.height() * bucket[key] / maximum)
                    path.lineTo(point) if connected else path.moveTo(point)
                    connected = True
                if key == "tokens" and count > 1:
                    area = QPainterPath(path)
                    area.lineTo(QPointF(xpos(count - 1), plot.bottom()))
                    area.lineTo(QPointF(xpos(0), plot.bottom()))
                    area.closeSubpath()
                    fill = QLinearGradient(plot.topLeft(), plot.bottomLeft())
                    upper, lower = QColor(color), QColor(color)
                    upper.setAlpha(46)
                    lower.setAlpha(4)
                    fill.setColorAt(0, upper)
                    fill.setColorAt(1, lower)
                    painter.fillPath(area, fill)
                pen = QPen(QColor(color), 2.4 if key in {"usd", "tokens"} else 2.0)
                if key == "usd":
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawPath(path)
                if count == 1 and self.buckets[0][key] is not None:
                    painter.drawEllipse(QPointF(xpos(0), plot.bottom() - plot.height() * self.buckets[0][key] / maximum), 3, 3)
            painter.setPen(text_pen)
            for rect, label, _index in geometry.time_ticks:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            if 0 <= self._hover < count:
                painter.setPen(QPen(QColor(c["muted"]), 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(xpos(self._hover), plot.top()), QPointF(xpos(self._hover), plot.bottom()))
        for (rect, key), (_key, label, color) in zip(self._legend, self.SERIES):
            x = rect.left()
            color = c.get("chart_cost" if key == "usd" else "chart_" + key, color)
            painter.setPen(QPen(QColor(color if key in self._enabled else c["muted"]), 3))
            painter.drawLine(QPointF(x + 3, rect.center().y()), QPointF(x + 19, rect.center().y()))
            painter.setPen(QColor(c["text"] if key in self._enabled else c["muted"]))
            painter.drawText(QRectF(x + 24, rect.top(), rect.width() - 24, rect.height()), Qt.AlignmentFlag.AlignVCenter, label)
        painter.end()

    def mousePressEvent(self, event) -> None:
        for rect, key in self._legend:
            if rect.contains(event.position()):
                self._enabled.symmetric_difference_update({key})
                self._tooltip.hide()
                self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self.buckets or not self._plot.contains(event.position()):
            self._hover = -1
            self._tooltip.hide()
            self.update()
            return
        index = min(len(self.buckets) - 1, max(0, round((event.position().x() - self._plot.left()) / self._plot.width() * (len(self.buckets) - 1))))
        self._hover = index
        self._tooltip.show_at(event.globalPosition().toPoint(), self._tooltip_text(self.buckets[index]), self._theme)
        self.update()

    def _tooltip_text(self, bucket: dict) -> str:
        lines = [bucket["timestamp"].strftime("%Y-%m-%d %H:%M"),
                 "Total Token  {:,}".format(bucket["tokens"]),
                 "价格  未定价" if bucket["usd"] is None else "价格  $%.6f" % bucket["usd"],
                 "请求数  {:,}".format(bucket["requests"])]
        for key, label, _color in self.SERIES:
            if key in self._enabled and key not in {"tokens", "usd"}:
                lines.append("{}  {:,}".format(label, bucket[key]))
        if bucket["unpriced"]:
            lines.append("{} 次请求未定价".format(bucket["unpriced"]))
        return "\n".join(lines)

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self._tooltip.hide()
        self.update()
        super().leaveEvent(event)

    def hideEvent(self, event) -> None:
        self._tooltip.hide()
        super().hideEvent(event)
