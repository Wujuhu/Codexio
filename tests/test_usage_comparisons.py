from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from codexio.charts import UsageChart, series_paths
from codexio.dashboard import Dashboard
from codexio.desktop_widgets import TokenComposition
from codexio.settings import AppSettings
from codexio.theme import theme_colors
from codexio.usage_queries import compare_usage, comparison_bounds


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def sample(stamp, tokens=100, cost=1, model="model-a", **extra):
    return dict(timestamp=stamp.isoformat(), total_tokens=tokens, input_tokens=tokens * 9 // 10,
                output_tokens=tokens // 10, cached_input_tokens=tokens // 2, cost_usd=cost,
                model=model, service_tier="default", pricing_status="priced" if cost is not None else "unpriced", **extra)


def test_today_comparison_uses_all_of_yesterday_and_ignores_future_records():
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    rows = [sample(now - timedelta(hours=1), 300, 3), sample(now - timedelta(days=1, hours=1), 200, 2),
            sample(now - timedelta(days=1) + timedelta(hours=1), 9000, 90), sample(now + timedelta(minutes=1), 9000, 90)]
    result = compare_usage(rows, "today", now)
    assert result["current"]["tokens"] == 300 and result["previous"]["tokens"] == 9200
    assert result["changes"]["tokens"]["percent"] == pytest.approx((300 - 9200) / 9200 * 100)
    assert result["changes"]["usd"]["percent"] == pytest.approx((3 - 92) / 92 * 100)
    assert result["changes"]["requests"]["percent"] == -50
    assert result["label"] == "较昨天"


@pytest.mark.parametrize("period,days", [("week", 7), ("month", 30)])
def test_comparison_uses_complete_previous_unit_without_overlapping_current(period, days):
    now = datetime.now().astimezone().replace(hour=15, minute=0, second=0, microsecond=0)
    start, end, previous_start, previous_end = comparison_bounds(period, now)
    assert previous_end + timedelta(microseconds=1) == start
    assert previous_end - previous_start + timedelta(microseconds=1) == timedelta(days=days)
    assert start - previous_start == timedelta(days=days)
    rows = [sample(start, 400, 4), sample(end, 200, 2), sample(previous_start, 100, 1), sample(previous_end, 200, 2),
            sample(previous_start - timedelta(microseconds=1), 10000, 100)]
    result = compare_usage(rows, period, now)
    assert result["changes"]["tokens"]["percent"] == 100 and result["changes"]["usd"]["percent"] == 100
    assert result["current"]["requests"] == result["previous"]["requests"] == 2
    assert "同期" not in result["label"]


def test_comparison_distinguishes_missing_history_zero_baseline_and_unknown_prices():
    now = datetime.now().astimezone().replace(hour=12)
    before = now - timedelta(days=1)
    assert compare_usage([sample(now)], "today", now)["changes"]["tokens"]["status"] == "no_history"
    assert compare_usage([sample(before)], "today", now)["changes"]["tokens"]["status"] == "no_current"
    result = compare_usage([sample(now, 100, 1), sample(before, 0, 0)], "today", now)
    assert result["changes"]["tokens"] == dict(status="zero_baseline", percent=None)
    result = compare_usage([sample(now, 200, None), sample(before, 100, 1)], "today", now)
    assert result["changes"]["usd"] == dict(status="no_current", percent=None)
    assert result["changes"]["tokens"]["percent"] == 100
    result = compare_usage([sample(now, 0, 0), sample(before, 0, 0)], "today", now)
    assert result["changes"]["tokens"]["percent"] == result["changes"]["usd"]["percent"] == 0
    assert compare_usage([], "all", now) is None


def test_combined_axes_lower_token_line_without_changing_data_or_zero_baselines(app):
    chart = UsageChart()
    chart.resize(900, 330)
    now = datetime.now().astimezone()
    buckets = [dict(timestamp=now + timedelta(hours=i), tokens=value, usd=value / 100, requests=1, unpriced=0) for i, value in enumerate((100, 200, 80, 300))]
    original = copy.deepcopy(buckets)
    chart.set_buckets(buckets)
    token_max, usd_max = chart.scale_maxima()
    geometry = chart._chart_geometry(chart.fontMetrics(), token_max, usd_max)
    plot = geometry.plot
    token_path, _ = series_paths(buckets, "tokens", plot, token_max)
    price_path, _ = series_paths(buckets, "usd", plot, usd_max)
    for i, bucket in enumerate(buckets):
        token, price = token_path.elementAt(i), price_path.elementAt(i)
        assert token.y > price.y
        assert (plot.bottom() - token.y) / plot.height() * token_max == pytest.approx(bucket["tokens"])
        assert (plot.bottom() - price.y) / plot.height() * usd_max == pytest.approx(bucket["usd"])
    assert token_path.boundingRect().top() > plot.center().y()
    assert chart._tooltip_text(buckets[-1]).find("300") >= 0
    chart.set_enabled_series({"tokens"})
    assert chart.scale_maxima()[0] < token_max
    assert buckets == original
    chart.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_token_segments_keep_true_proportions_bright_colors_and_matching_legends(app, theme):
    widget = TokenComposition()
    widget.resize(340, 140)
    widget.set_theme(theme)
    widget.set_buckets([dict(cache_read=70, input=20, output=10)])
    widget.show()
    app.processEvents()
    segments = widget.segment_geometry()
    assert [rect.height() for _, _, rect in segments] == [11, 11, 11]
    assert segments[0][2].width() / segments[1][2].width() == pytest.approx(3.5)
    assert segments[1][2].width() / segments[2][2].width() == pytest.approx(2)
    assert all(right[2].left() - left[2].right() == pytest.approx(4) for left, right in zip(segments, segments[1:]))
    colors = theme_colors(theme)
    image = widget.grab().toImage()
    for label, key, rect in segments:
        assert image.pixelColor(rect.center().toPoint()).name() == colors[key].lower()
        legend = next(row for row in widget.legend_geometry() if row[0] == label)
        point = QPointF(legend[3].left() - 10, legend[3].center().y()).toPoint()
        assert image.pixelColor(point).name() == colors[key].lower()
    widget.set_buckets([])
    assert widget.segment_geometry() == []
    widget.close()


def test_overview_and_model_filtered_trends_display_matching_percentage_changes(app):
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [sample(start, 400, 2, id="now-a", session_id="a", turn_id="now"),
            sample(start, 100, 3, model="model-b", id="now-b", session_id="b", turn_id="now"),
            sample(start - timedelta(days=1), 200, 1, id="old-a", session_id="a", turn_id="old"),
            sample(start - timedelta(days=1), 300, 5, model="model-b", id="old-b", session_id="b", turn_id="old")]
    window = Dashboard(AppSettings(), {"theme": "light"}, {})
    window.apply_data(dict(records=rows, available_models=["model-a", "model-b"], sources_complete=True))
    window.open_page("overview")
    assert window._overview_comparisons["usd"].value.text() == "−16.7%"
    assert window._overview_comparisons["tokens"].value.text() == "0.0%"
    window.open_page("trends")
    window._trend_model.setCurrentIndex(window._trend_model.findData("model-a"))
    assert window._trend_comparisons["tokens"].value.text() == "+100.0%"
    assert window._trend_comparisons["usd"].value.text() == "+100.0%"
    assert window._trend_metric_values["tokens"].text() == "400"
    window._trend_period.setCurrentIndex(window._trend_period.findData("all"))
    assert not window._trend_metrics_box.isVisible()
    window.open_page("overview")
    window.apply_data(dict(records=rows, sources_complete=False))
    assert window._overview_comparisons["tokens"].value.text() == "0.0%"
    window.close()
