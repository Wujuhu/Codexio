"""Native, interactive token/cost chart and local-time aggregation helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFontMetrics, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QFrame, QGridLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from codexio.theme import theme_colors
from codexio.money import usd
from codexio.confirmed_usage import ConfirmedUsage, confirmed_count, confirmed_tokens


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
    confirmed = {}
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
        point = floor(stamp)
        b = values.setdefault(point, empty(point))
        if point not in confirmed:
            confirmed[point] = ConfirmedUsage()
        confirmed[point].add(row)
        parts = [confirmed_count(row.get(key, 0)) for key in
                 ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")]
        if confirmed_tokens(row) is not None and all(value is not None for value in parts):
            inp, cached, written, out = parts
            if cached + written <= inp:
                b["input"] += inp - cached - written
                b["cache_read"] += cached
                b["cache_write"] += written
                b["output"] += out
        b["requests"] += 1
    if not values:
        return []
    for point, bucket in values.items():
        summary = confirmed[point].summary()
        bucket.update(tokens=summary["tokens"], usd=summary["usd"], unpriced=summary["skipped"]["usd"])
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
                 "请求数": "muted", "暂无记录": "muted"}
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
            color_key = known.get(name, "warning")
            color = colors.get(color_key + "_ink", colors.get(color_key, colors["text"]))
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
    """Keep large axes compact and dollar labels at two decimal places."""
    if currency:
        return usd(value, compact=True)
    for limit, suffix in ((1e18, "E"), (1e15, "P"), (1e12, "T")):
        if abs(value) >= limit:
            text = ("%.2f" % (value / limit)).rstrip("0").rstrip(".") + suffix
            break
    else:
        text = compact_number(value)
    return text


def series_paths(buckets, key, plot, maximum):
    """Close each known interval separately so missing prices remain gaps."""
    line, areas, segment = QPainterPath(), [], []
    count = len(buckets)

    def flush():
        if not segment:
            return
        line.moveTo(segment[0])
        for point in segment[1:]:
            line.lineTo(point)
        if len(segment) > 1:
            area = QPainterPath(segment[0])
            for point in segment[1:]:
                area.lineTo(point)
            area.lineTo(QPointF(segment[-1].x(), plot.bottom()))
            area.lineTo(QPointF(segment[0].x(), plot.bottom()))
            area.closeSubpath()
            areas.append(area)
        segment.clear()

    for index, bucket in enumerate(buckets):
        value = bucket.get(key)
        if value is None:
            flush()
        else:
            segment.append(QPointF(plot.left() + plot.width() * index / max(1, count - 1),
                                  plot.bottom() - plot.height() * value / maximum))
    flush()
    return line, areas


class UsageChart(QWidget):
    bucket_clicked = Signal(dict)
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
        self._compact = False

    def set_compact(self, value=True):
        self._compact = bool(value)
        self._update_minimum_chart_height()
        self.update()

    def set_enabled_series(self, names):
        self._enabled = set(names).intersection(key for key, _, _ in self.SERIES)
        self._tooltip.hide()
        self.update()

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
        minimum = 260 if getattr(self, "_compact", False) else 310
        if self.minimumHeight() != max(minimum, required):
            self.setMinimumHeight(max(minimum, required))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._update_minimum_chart_height()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_minimum_chart_height()

    def _layout_legend(self, metrics) -> list[tuple[QRectF, str]]:
        series = self._legend_series()
        item_width = max(metrics.horizontalAdvance(label) + 38 for _, label, _ in series)
        row_height = max(29, metrics.height() + 10)
        columns = min(len(series), max(1, int((self.width() - 20) // item_width)))
        rows = math.ceil(len(series) / columns)
        columns = math.ceil(len(series) / rows)
        rects = []
        for row in range(rows):
            items = series[row * columns:(row + 1) * columns]
            left = (self.width() - len(items) * item_width) / 2
            top = self.height() - 6 - (rows - row) * row_height
            for column, (key, _label, _color) in enumerate(items):
                rects.append((QRectF(left + column * item_width, top, item_width, row_height - 2), key))
        return rects

    def _legend_series(self):
        return tuple(item for item in self.SERIES if item[0] in ("usd", "tokens")) if getattr(self, "_compact", False) else self.SERIES

    def scale_maxima(self):
        token_series = self._enabled - {"usd"}
        token_peak = max((b.get(key) or 0 for b in self.buckets for key in token_series), default=0)
        price_peak = max((b["usd"] for b in self.buckets if b.get("usd") is not None), default=0)
        # Both axes keep a true zero baseline. Extra Token headroom separates
        # otherwise proportional lines; its labelled scale changes accordingly.
        token_headroom = 2.2 if "usd" in self._enabled and token_series else 1.12
        return max(1, token_peak * token_headroom), max(.01, price_peak * 1.12)

    def _chart_geometry(self, metrics, token_max: float, usd_max: float) -> _ChartGeometry:
        """Use the actual paint font for every label, including scaled Windows fonts."""
        text_height = metrics.height() + 2
        text_gap = max(10, math.ceil(text_height * 0.3))
        legend = self._layout_legend(metrics)
        legend_top = min((rect.top() for rect, _ in legend), default=self.height() - 6)
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
        token_max, usd_max = self.scale_maxima()
        geometry = self._chart_geometry(painter.fontMetrics(), token_max, usd_max)
        self._legend, self._plot = geometry.legend, geometry.plot
        plot = self._plot
        text_pen = QPen(QColor(c.get("chart_text", c["muted"])))
        painter.setPen(QColor(c["chart_tokens_ink"]))
        painter.drawText(geometry.token_title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Total Token")
        painter.setPen(QColor(c["chart_cost_ink"]))
        painter.drawText(geometry.cost_title, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "价格")
        for (token_rect, token_label), (cost_rect, cost_label) in zip(geometry.token_ticks, geometry.cost_ticks):
            y = token_rect.center().y()
            grid = QColor(c["grid"])
            grid.setAlpha(130)
            painter.setPen(QPen(grid, 1))
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

            paths = []
            for key, _label, color in reversed(self.SERIES):
                if key not in self._enabled:
                    continue
                color = c.get("chart_cost" if key == "usd" else "chart_" + key, color)
                maximum = usd_max if key == "usd" else token_max
                path, areas = series_paths(self.buckets, key, plot, maximum)
                paths.append((key, color, path, maximum))
                fill = QLinearGradient(plot.topLeft(), plot.bottomLeft())
                upper, lower = QColor(color), QColor(color)
                upper.setAlpha(28 if len(self._enabled) <= 2 else 18)
                lower.setAlpha(4 if len(self._enabled) <= 2 else 3)
                fill.setColorAt(0, upper)
                fill.setColorAt(1, lower)
                for area in areas:
                    painter.fillPath(area, fill)
            # Stroke after every fill, keeping all curve boundaries legible.
            for key, color, path, maximum in paths:
                pen = QPen(QColor(color), 2.0 if key in {"usd", "tokens"} else 1.7)
                painter.setPen(pen)
                painter.drawPath(path)
            # Keep every recorded point visible, including without hover. Missing
            # prices retain their gaps; disabled series do not leave markers.
            radius = min(3.4, max(1.6, plot.width() / max(1, count - 1) / 4))
            for key, color, _, maximum in paths:
                painter.setPen(QPen(QColor(c["surface"]), 1.2))
                painter.setBrush(QColor(color))
                for index, bucket in enumerate(self.buckets):
                    value = bucket.get(key)
                    if value is not None:
                        painter.drawEllipse(QPointF(xpos(index), plot.bottom() - plot.height() * value / maximum), radius, radius)
            painter.setPen(text_pen)
            for rect, label, _index in geometry.time_ticks:
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            if 0 <= self._hover < count:
                painter.setPen(QPen(QColor(c["muted"]), 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(xpos(self._hover), plot.top()), QPointF(xpos(self._hover), plot.bottom()))
                for key, color, _, maximum in paths:
                    value = self.buckets[self._hover].get(key)
                    if value is not None:
                        painter.setPen(QPen(QColor(c["surface"]), 1.5))
                        painter.setBrush(QColor(color))
                        painter.drawEllipse(QPointF(xpos(self._hover), plot.bottom() - plot.height() * value / maximum), 4.2, 4.2)
        for (rect, key), (_key, label, color) in zip(self._legend, self._legend_series()):
            x = rect.left()
            color = c.get("chart_cost" if key == "usd" else "chart_" + key, color)
            legend_pen = QPen(QColor(color), 3)
            painter.setPen(legend_pen)
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
        if self.buckets and self._plot.contains(event.position()):
            index = min(len(self.buckets) - 1, max(0, round((event.position().x() - self._plot.left()) / max(1, self._plot.width()) * (len(self.buckets) - 1))))
            self.bucket_clicked.emit(self.buckets[index])
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
                 "价格  " + usd(bucket["usd"]),
                 "Total Token  暂无有效数据" if bucket["tokens"] is None else "Total Token  {:,}".format(bucket["tokens"]),
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
