from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from aiquota.charts import UsageChart
from aiquota.theme import apply_theme, ensure_ui_fonts


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    ensure_ui_fonts()
    return instance


def buckets(tokens=102_214_286, usd=164.553571):
    start = datetime(2026, 9, 1).astimezone()
    return [dict(timestamp=start + timedelta(hours=index), tokens=tokens,
                 input=tokens, cache_read=0, cache_write=0, output=0,
                 usd=usd, requests=1, unpriced=int(usd is None)) for index in range(24)]


def geometry(chart):
    token_max = max(1, max((row["tokens"] for row in chart.buckets), default=0) * 1.12)
    usd_max = max(0.01, max((row["usd"] or 0 for row in chart.buckets), default=0) * 1.12)
    return chart._chart_geometry(chart.fontMetrics(), token_max, usd_max)


def set_chart_font(chart, pixels, theme):
    chart.set_theme(theme)
    font = chart.font()
    font.setFamily("Microsoft YaHei UI")
    font.setPixelSize(pixels)
    chart.setFont(font)


def assert_readable_geometry(chart, layout):
    bounds = QRectF(chart.rect())
    metrics = chart.fontMetrics()
    assert layout.plot.width() > 60
    assert layout.plot.height() >= max(120, metrics.height() * 3)
    for rect, text in (layout.token_ticks + layout.cost_ticks +
                       [(layout.token_title, "Total Token"), (layout.cost_title, "价格")]):
        assert bounds.contains(rect), (rect, text)
        assert rect.width() >= metrics.horizontalAdvance(text), text
        assert rect.height() >= metrics.height(), text
    assert layout.token_ticks[-1][0].top() - layout.token_title.bottom() >= 10
    assert layout.cost_ticks[-1][0].top() - layout.cost_title.bottom() >= 10
    for ticks in (layout.token_ticks, layout.cost_ticks):
        for (rect, _text), (other, _next) in zip(ticks, ticks[1:]):
            assert not rect.intersects(other)
    for index, (rect, label, _bucket_index) in enumerate(layout.time_ticks):
        assert bounds.contains(rect), label
        assert rect.width() >= metrics.horizontalAdvance(label)
        assert rect.top() > layout.token_ticks[0][0].bottom()
        assert all(not rect.intersects(other) for other, _text, _ in layout.time_ticks[index + 1:])
        assert rect.bottom() < min(item.top() for item, _key in layout.legend)
    for index, (rect, key) in enumerate(layout.legend):
        assert bounds.contains(rect), key
        assert all(not rect.intersects(other) for other, _ in layout.legend[index + 1:])
        label = next(label for candidate, label, _ in chart.SERIES if candidate == key)
        assert rect.width() - 24 >= metrics.horizontalAdvance(label)
        assert rect.height() >= metrics.height()


@pytest.mark.parametrize("width", [440, 940])
@pytest.mark.parametrize("font_pixels", [13, 17, 20, 26])
@pytest.mark.parametrize("granularity", ["hour", "day"])
def test_scaled_fonts_keep_axis_titles_values_dates_and_legends_separate(app, width, font_pixels, granularity):
    chart = UsageChart()
    # This also exercises enlarged text independently of device pixel scaling.
    set_chart_font(chart, font_pixels, "dark")
    chart.resize(width, 400)
    chart.buckets = buckets()
    chart._granularity = granularity
    chart.show()
    app.processEvents()
    assert chart.font().pixelSize() == font_pixels
    layout = geometry(chart)
    assert_readable_geometry(chart, layout)
    assert layout.token_ticks[-1][1] == "114.48M"
    assert layout.plot == chart._plot
    assert not chart.grab().isNull()
    chart.close()


@pytest.mark.parametrize("records", [[], buckets(0, 0), buckets(123_456_789_012_345, 123_456_789.25), buckets(12_000, None)])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_empty_unpriced_and_large_values_fit_at_minimum_size(app, records, theme):
    chart = UsageChart()
    set_chart_font(chart, 26, theme)
    chart.resize(440, 310)
    chart.buckets = records
    chart.show()
    app.processEvents()
    assert chart.font().pixelSize() == 26
    layout = geometry(chart)
    assert_readable_geometry(chart, layout)
    if not records:
        text_bounds = chart.fontMetrics().boundingRect(
            layout.plot.toAlignedRect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
            "当前时间范围\n暂无请求记录")
        assert text_bounds.height() <= layout.plot.height()
    assert not chart.grab().isNull()
    if records and records[0]["usd"] == 123_456_789.25:
        assert "123456789.250000" in chart._tooltip_text(records[0])
    chart.close()


def test_hover_bucket_and_legend_targets_follow_resized_plot(app):
    chart = UsageChart()
    apply_theme(chart, "dark")
    chart.buckets = buckets()
    chart.show()
    for width in (940, 440):
        chart.resize(width, 400)
        app.processEvents()
        for fraction in (0.0, 0.5, 1.0):
            point = chart._plot.center()
            point.setX(chart._plot.left() + chart._plot.width() * fraction)
            move = QMouseEvent(QEvent.Type.MouseMove, point, QPointF(chart.mapToGlobal(point.toPoint())),
                               Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(chart, move)
            assert chart._hover == round(fraction * 23)
        rect = next(rect for rect, key in chart._legend if key == "cache_read")
        before = "cache_read" in chart._enabled
        QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
        assert ("cache_read" in chart._enabled) is not before
    chart.close()
