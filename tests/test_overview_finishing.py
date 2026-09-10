from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QLabel

from codexio.charts import UsageChart
from codexio.dashboard import Dashboard
from codexio.desktop_widgets import QuotaMeter
from codexio.settings import AppSettings
from codexio.theme import theme_colors


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_overview_metrics_keep_percentages_without_bottom_annotation_labels(app):
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [dict(id=str(day), session_id="session", turn_id=str(day), timestamp=(start - timedelta(days=day)).isoformat(),
                 model="gpt-6-astra", total_tokens=100, input_tokens=90, output_tokens=10, cost_usd=1,
                 pricing_status="priced", prompt_preview="测试请求") for day in (0, 1)]
    window = Dashboard(AppSettings(), {"theme": "light"}, {})
    window.apply_data(dict(records=rows, sources_complete=True))
    window.open_page("overview")
    app.processEvents()
    for value in (window._overview_cost, window._overview_tokens, window._overview_calls):
        labels = [label.text() for label in value.parentWidget().findChildren(QLabel)]
        assert "按当前模型价格" not in labels and "输入 + 输出" not in labels
        assert not any("次用户请求" in label for label in labels)
    assert all(widget.isVisible() and widget.value.text() == "0.0%" for widget in window._overview_comparisons.values())
    window.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_home_quota_bars_use_smooth_level_colors_and_full_token_bar_height(app, theme):
    meter = QuotaMeter("本周额度")
    meter.resize(320, 100)
    meter.set_theme(theme)
    meter.show()
    colors = theme_colors(theme)
    for remaining, key in ((0, "quota_low"), (50, "quota_mid"), (100, "quota_high")):
        meter.set_value(remaining, "重置明天")
        assert meter.progress_color().name() == colors[key].lower()
    meter.set_value(25, "重置明天")
    middle = meter.progress_color()
    meter.set_value(24, "重置明天")
    assert all(abs(a-b) <= 2 for a, b in zip(middle.getRgb(), meter.progress_color().getRgb()))
    meter.set_value(75, "重置明天")
    app.processEvents()
    image = meter.grab().toImage()
    assert image.pixelColor(50, 47).name() == meter.progress_color().name()
    assert image.pixelColor(100, 51).name() == meter.progress_color().name()
    assert image.pixelColor(290, 47).name() == colors["raised"].lower()
    for missing in (None, 0):
        meter.set_value(missing, "重置 —")
        app.processEvents()
        assert meter.grab().toImage().pixelColor(50, 47).name() == colors["raised"].lower()
    meter.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chart_points_are_visible_without_hover_and_tooltip_lists_price_first(app, theme):
    chart = UsageChart()
    chart.resize(900, 350)
    chart.set_theme(theme)
    start = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
    rows = [dict(timestamp=start + timedelta(hours=i), tokens=tokens, usd=cost, requests=1,
                 unpriced=int(cost is None)) for i, (tokens, cost) in enumerate(((100, 1), (80, None), (200, 2)))]
    chart.set_buckets(rows)
    chart.show()
    app.processEvents()
    chart._hover = -1
    image = chart.grab().toImage()
    token_max, usd_max = chart.scale_maxima()
    plot = chart._plot
    colors = theme_colors(theme)
    for key, maximum, color in (("usd", usd_max, colors["chart_cost"]), ("tokens", token_max, colors["chart_tokens"])):
        for index, row in enumerate(rows):
            if row[key] is not None:
                point = QPointF(plot.left() + plot.width()*index/2, plot.bottom() - plot.height()*row[key]/maximum)
                assert image.pixelColor(point.toPoint()).name() == color.lower()
    first_price = QPointF(plot.left(), plot.bottom() - plot.height()/usd_max).toPoint()
    chart.set_enabled_series({"tokens"})
    app.processEvents()
    assert chart.grab().toImage().pixelColor(first_price).name() != colors["chart_cost"].lower()
    text = chart._tooltip_text(rows[0])
    assert text.splitlines()[1].startswith("价格  $")
    assert text.splitlines()[2].startswith("Total Token")
    chart._tooltip._set_content(text, colors)
    assert [row["name"] for row in chart._tooltip.rows[:3]] == ["价格", "Total Token", "请求数"]
    assert chart._tooltip_text(rows[1]).splitlines()[1] == "价格  未定价"
    chart.close()
