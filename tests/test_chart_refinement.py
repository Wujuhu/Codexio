from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

from aiquota.charts import UsageChart, bucket_records
from aiquota.theme import apply_theme, ensure_ui_fonts


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    ensure_ui_fonts()
    return instance


def sample(**updates):
    value = dict(timestamp=datetime.now().astimezone().isoformat(), input_tokens=1000,
                 cached_input_tokens=700, cache_write_input_tokens=100, output_tokens=200,
                 reasoning_output_tokens=50, total_tokens=1200, cost_usd=0.04)
    value.update(updates)
    return value


@pytest.mark.parametrize("width", [440, 940])
def test_default_series_and_legends_remain_clickable_without_clipping(app, width):
    chart = UsageChart()
    apply_theme(chart, "dark")
    chart.resize(width, 400)
    chart.set_records([sample()])
    chart.show()
    app.processEvents()
    assert chart._enabled == {"usd", "tokens"}
    assert len(chart._legend) == 6
    bounds = chart.rect()
    for rect, _key in chart._legend:
        assert bounds.contains(rect.toAlignedRect())
        assert rect.top() > chart._plot.bottom() + 30
    for index, (rect, _key) in enumerate(chart._legend):
        assert all(not rect.intersects(other) for other, _ in chart._legend[index + 1:])
    rect = next(rect for rect, key in chart._legend if key == "cache_write")
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert chart._enabled == {"usd", "tokens", "cache_write"}
    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert chart._enabled == {"usd", "tokens"}
    chart.close()


def test_total_counts_cache_and_reasoning_as_subsets_and_preserves_unknown_cost():
    now = datetime.now().astimezone()
    buckets = bucket_records([sample(cost_usd=None)], "today", "hour", now=now + timedelta(seconds=1))
    occupied = next(bucket for bucket in buckets if bucket["requests"])
    assert occupied["tokens"] == 1200
    assert occupied["input"] == 200
    assert occupied["cache_read"] == 700
    assert occupied["cache_write"] == 100
    assert occupied["output"] == 200
    assert occupied["usd"] is None
    assert occupied["unpriced"] == 1


def test_tooltip_shows_overview_then_only_enabled_breakdowns(app):
    chart = UsageChart()
    chart.set_records([sample()])
    bucket = next(bucket for bucket in chart.buckets if bucket["requests"])
    tooltip = chart._tooltip_text(bucket)
    assert "Total Token  1,200" in tooltip
    assert "价格  $0.040000" in tooltip
    assert "请求数  1" in tooltip
    assert not any(label in tooltip for label in ("普通输入", "缓存创建", "缓存利用", "输出"))
    chart._enabled.update({"cache_write", "cache_read", "input", "output"})
    tooltip = chart._tooltip_text(bucket)
    for part in ("缓存创建  100", "缓存利用  700", "普通输入  200", "输出  200"):
        assert part in tooltip
    bucket.update(usd=None, unpriced=1)
    assert "价格  未定价" in chart._tooltip_text(bucket)
    assert "1 次请求未定价" in chart._tooltip_text(bucket)
    chart.close()


def test_tooltip_font_is_readable_and_local_and_hides_with_chart(app):
    original_font = QToolTip.font()
    chart = UsageChart()
    apply_theme(chart, "dark")
    chart.resize(640, 400)
    chart.set_records([sample()])
    chart.show()
    app.processEvents()
    chart._tooltip.show_at(QPoint(20, 20), "Total Token  1,200\n价格  $0.04", "dark")
    app.processEvents()
    assert chart._tooltip.isVisible()
    assert chart._tooltip.rows[0]["value_label"].font().pixelSize() == 16
    assert chart._tooltip.rows[0]["value_label"].textFormat() == Qt.TextFormat.PlainText
    assert chart._tooltip.rows[0]["color"] != chart._tooltip.rows[1]["color"]
    assert QToolTip.font() == original_font
    chart.hide()
    assert not chart._tooltip.isVisible()
    chart.close()


def test_tooltip_wraps_long_totals_and_grows_vertically(app):
    chart = UsageChart()
    tooltip = chart._tooltip
    tooltip.show_at(QPoint(20, 20), "Total Token  1,200\n价格  $0.04", "dark")
    app.processEvents()
    compact_height = tooltip.height()
    text = "Total Token  123,456,789,012,345,678\n价格  $123,456,789.123456\n请求数  1,234,567"
    tooltip.show_at(QPoint(20, 20), text, "dark")
    app.processEvents()
    assert tooltip.width() <= 300
    assert tooltip.height() > compact_height
    assert tooltip.plain_text == text
    assert all(row["value_label"].wordWrap() for row in tooltip.rows)
    assert all(row["value_label"].height() >= row["value_label"].heightForWidth(row["value_label"].width()) for row in tooltip.rows)
    chart.close()


def test_tooltip_reuses_colored_rows_when_only_pointer_moves(app):
    chart = UsageChart()
    tooltip = chart._tooltip
    text = "2026/09/07 18:00\nTotal Token  123,456\n价格  $12.340000\n请求数  3"
    tooltip.show_at(QPoint(20, 20), text, "dark")
    first = tooltip.rows[0]["value_label"]
    tooltip.show_at(QPoint(30, 20), text, "dark")
    assert tooltip.title_label.text() == "2026/09/07 18:00"
    assert tooltip.rows[0]["value_label"] is first
    chart.close()
