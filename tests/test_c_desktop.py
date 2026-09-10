"""Behavioral regressions for the approved native desktop workflow."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from codexio.analytics_config import NAVIGATION_PAGES, load_analytics_config, save_analytics_config, normalize_subscription_profile
from codexio.dashboard import Dashboard, PAGE_NAMES
from codexio.desktop_widgets import TokenComposition, preview_title
from codexio.rate_limits import QuotaStatus, parse_rate_limits_result, quota_state_from_snapshot
from codexio.settings import AppSettings


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def records(count=1):
    now = datetime.now(timezone.utc)
    return [dict(id=str(i), session_id="session-" + str(i), turn_id="turn", timestamp=(now - timedelta(seconds=i)).isoformat(),
                 model="model", service_tier="default", source_id="local", source_name="本机", input_tokens=100,
                 output_tokens=10, total_tokens=110, cost_usd=1, pricing_status="priced", prompt_preview="请求 " + str(i)) for i in range(count)]


def test_navigation_reorders_persists_and_routes_by_identity(app, tmp_path):
    path = tmp_path / "analytics.json"
    config = dict(navigation_order=["logs", "overview", "logs", "invalid", "settings"], theme="dark")
    save_analytics_config(config, path)
    saved = load_analytics_config(path)
    window = Dashboard(AppSettings(), saved, {"config": lambda value: save_analytics_config(value, path)})
    window.open_page("pricing")
    nav = window._navigation
    assert nav.order()[:3] == ["overview", "logs", "settings"]
    assert not nav.move_page("overview", 4)
    assert nav.move_page("pricing", 0)
    assert nav.order()[:2] == ["overview", "pricing"]
    assert window._active_page == "pricing" and window._stack.currentIndex() == PAGE_NAMES.index("pricing")
    assert not (nav.item(0).flags() & Qt.ItemFlag.ItemIsDragEnabled)
    nav.page_requested.emit("logs")
    assert window._stack.currentWidget() is window._pages["logs"]
    assert load_analytics_config(path)["navigation_order"] == nav.order()
    window.close()


@pytest.mark.parametrize("width", [240, 350, 650])
def test_token_legend_keeps_labels_with_their_percentages(app, width):
    composition = TokenComposition()
    composition.set_buckets([dict(cache_read=77, input=19, output=4)])
    pairs = composition.legend_geometry(width)
    assert [pair[1] for pair in pairs] == ["77%", "19%", "4%"]
    for _, _, _, label, value in pairs:
        assert value.left() - label.right() == 6
        assert label.top() == value.top()
        assert value.right() <= width - 16
    for previous, current in zip(pairs, pairs[1:]):
        if previous[3].top() == current[3].top():
            assert current[3].left() - previous[4].right() >= 20
    composition.close()


def test_second_page_inspector_and_search_keep_the_correct_record_and_focus(app):
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data({"records": records(121)})
    window.open_page("logs", "all")
    window._change_page(1)
    expected = window._rendered_log_rows[0]
    window._inspect_log_row(0)
    assert window._inspected_record["id"] == expected["id"]
    assert any(field.text() == expected["session_id"] for field in window._inspector_scroll.findChildren(QLineEdit))
    window._close_inspector()
    assert window._log_table.currentRow() == -1 and window._inspector_empty.isVisible()
    window._log_search.setFocus()
    QTest.keyClicks(window._log_search, "session-120")
    QTest.qWait(260)
    assert window._log_search.hasFocus()
    assert window._page_number == 0 and window._log_table.rowCount() == 1
    assert window._rendered_log_rows[0]["session_id"] == "session-120"
    window.close()


def test_credit_panel_and_history_share_one_continuous_subscription_page(app):
    now = datetime.now(timezone.utc)
    payload = {"rateLimits": {"planType": "pro"}, "rateLimitResetCredits": {"availableCount": 4, "credits": [
        {"id": "known", "expiresAt": int((now + timedelta(days=8)).timestamp()), "status": "available"},
        {"id": "unlimited", "expiresAt": None, "status": "available"}, {"id": "unknown", "status": "available"}]}}
    state = quota_state_from_snapshot(parse_rate_limits_result(payload), status=QuotaStatus.OK, message="")
    window = Dashboard(AppSettings(), {"subscription_profile": {"plan": "Pro 20X", "price_usd": 200}}, {})
    window.apply_quota(state)
    window.open_page("subscription")
    app.processEvents()
    page = window._pages["subscription"]
    assert window._reset_count.text() == "4 次可用"
    view = window._reset_details_table
    assert view.rowCount() == 3
    deadlines = [view.item(row, 1).text() for row in range(3)]
    assert "无到期限制" in deadlines and "截止时间未提供" in deadlines
    dated = next(text for text in deadlines if text.startswith("20"))
    assert len(dated.splitlines()) == 2 and ":" in dated.splitlines()[1]
    assert dated.replace("\n", " ") in view.item(0, 1).toolTip()
    assert "另有 1 次" in window._reset_details_note.text()
    assert not hasattr(window, "_subscription_tabs") and not hasattr(window, "_subscription_stack")
    assert page.widget().isAncestorOf(window._subscription_history)
    assert window._subscription_history_section.y() > window._reset_details_table.mapTo(page.widget(), window._reset_details_table.rect().bottomLeft()).y()
    page.ensureWidgetVisible(window._subscription_history, 0, 12)
    app.processEvents()
    assert page.verticalScrollBar().value() > 0
    assert window._reset_count.text() == "4 次可用"
    window.close()


def test_widget_preview_is_real_and_does_not_save_until_requested(app):
    saved = []
    window = Dashboard(AppSettings(), {}, {"settings": saved.append})
    window.open_page("settings")
    window._settings_sections.setCurrentRow(1)
    field = window._setting_widgets["visual_style"]
    field.setCurrentIndex(field.findData("orb"))
    window._setting_widgets["background_opacity"].setValue(40)
    app.processEvents()
    assert window._widget_preview._content.__class__.__name__ == "OrbStyle"
    assert window._widget_preview._settings.background_opacity == 40
    assert window._settings.visual_style == "classic" and saved == []
    assert window._widget_preview._content._timer.isActive()
    window.open_page("logs")
    assert not window._widget_preview._content._timer.isActive()
    window.open_page("settings")
    window._save_settings()
    assert saved[0].visual_style == "orb" and saved[0].background_opacity == 40
    window.close()


def test_missing_prompt_uses_explicit_source_fallback_and_profile_is_not_inferred():
    assert preview_title({"prompt_preview": "真实输入", "session_title": "title"}) == "真实输入"
    assert preview_title({"session_title": "title"}) == "会话：title"
    assert preview_title({"session_id": "abcdef"}) == "Session abcdef"
    assert normalize_subscription_profile(None) == dict(plan="", price_usd=None, renewal_date="")
    assert normalize_subscription_profile(dict(price_usd=float("nan"), renewal_date="2026-02-30"))["price_usd"] is None


def test_existing_ledger_colors_follow_theme_without_new_data(app):
    from codexio.desktop_widgets import PRIMARY_COLOR_ROLE
    from codexio.theme import theme_colors
    window = Dashboard(AppSettings(), {"theme": "dark"}, {})
    row = records()[0]
    window.apply_data({"records": [row], "turns": [dict(session_id=row["session_id"], turn_id="turn", verified=True,
        started_at=row["timestamp"], status="running", started_inferred=False)]})
    window.open_page("logs", "all")
    table = window._log_table
    assert table.item(0, 6).data(PRIMARY_COLOR_ROLE) == "running"
    assert table.item(0, 6).font().bold()
    window.config_updated({"theme": "light"})
    app.processEvents()
    assert table._theme == "light"
    image = table.viewport().grab().toImage()
    table.scrollToItem(table.item(0, 6))
    app.processEvents()
    image = table.viewport().grab().toImage()
    ink = theme_colors("light")["running"].lower()
    rect = table.visualItemRect(table.item(0, 6))
    assert any(image.pixelColor(x, y).name() == ink for y in range(rect.top(), rect.bottom())
               for x in range(rect.left(), rect.right()))
    def luminance(color):
        values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    colors = theme_colors("light")
    for foreground in ("success", "muted"):
        for background in ("bg", "selection", "surface"):
            assert (luminance(colors[background]) + .05) / (luminance(colors[foreground]) + .05) >= 4.5
    window.close()


@pytest.mark.parametrize("width", [920, 1280, 1600])
def test_overview_equal_thirds_keep_complete_legend_pairs(app, width):
    window = Dashboard(AppSettings(), {"theme": "dark"}, {})
    row = dict(records()[0], input_tokens=1000, cached_input_tokens=500, cache_write_input_tokens=100,
               output_tokens=200, total_tokens=1200)
    window.resize(width, 660)
    window.apply_data({"records": [row]})
    window.open_page("overview")
    app.processEvents()
    widgets = [window._quota_widgets["overview"][key] for key in ("five_hour", "week")] + [window._token_composition]
    widths = [widget.width() for widget in widgets]
    assert max(widths) - min(widths) <= 1
    assert all(widget.geometry().top() == widgets[0].geometry().top() for widget in widgets)
    for _, _, _, label, value in window._token_composition.legend_geometry():
        assert value.right() <= widgets[-1].width() - 16
        assert value.bottom() < widgets[-1].height()
        assert value.left() - label.right() == 6
    window.close()


def test_usage_defaults_to_today_and_overview_shows_two_scales_together(app):
    from PySide6.QtCore import QRectF
    now = datetime.now().astimezone()
    window = Dashboard(AppSettings(), {}, {})
    today = dict(records()[0], timestamp=now.isoformat(), total_tokens=1200, input_tokens=1190, cost_usd=2)
    yesterday = dict(today, id="yesterday", session_id="yesterday", timestamp=(now - timedelta(days=1)).isoformat(), total_tokens=9000, input_tokens=8990, cost_usd=20)
    window.apply_data({"records": [today, yesterday]})
    window.open_page("overview")
    app.processEvents()
    chart = window._overview_chart
    assert window._overview_period.value() == "today" and chart._granularity == "hour"
    assert sum(bucket["tokens"] for bucket in chart.buckets) == 1200
    assert sum(bucket["usd"] or 0 for bucket in chart.buckets) == 2
    assert chart._enabled == {"usd", "tokens"}
    assert {key for _, key in chart._legend} == {"usd", "tokens"}
    geometry = chart._chart_geometry(chart.fontMetrics(), 1200, 2)
    assert geometry.plot.height() >= 120
    assert all(text.startswith("$") and rect.width() > 0 for rect, text in geometry.cost_ticks)
    assert all(not text.startswith("$") for _, text in geometry.token_ticks)
    assert QRectF(chart.rect()).contains(geometry.cost_title)
    assert all(rect.left() > geometry.plot.right() for rect, _ in geometry.cost_ticks)
    assert not hasattr(window, "_overview_measure")
    window.open_page("trends")
    assert window._trend_period.currentData() == "today" and window._granularity.currentData() == "hour"
    assert sum(bucket["tokens"] for bucket in window._trend_chart.buckets) == 1200
    window.open_page("logs")
    assert window._log_period.currentData() == "today"
    assert len(window._filtered_records) == 1
    window.close()


def test_explicit_period_and_restored_selection_override_today_without_hiding_either_overview_series(app):
    window = Dashboard(AppSettings(), {}, {})
    window.restore_view_state({"overview": {"_overview_period": "month", "_overview_measure": "usd"},
                               "trends": {"_trend_period": "week", "_granularity": "day"}})
    window.apply_data({"records": records()})
    window.open_page("overview")
    assert window._overview_period.value() == "month"
    assert window._overview_chart._enabled == {"usd", "tokens"}
    window.open_page("trends")
    assert window._trend_period.currentData() == "week" and window._granularity.currentData() == "day"
    window.open_page("trends", "today")
    assert window._trend_period.currentData() == "today" and window._granularity.currentData() == "hour"
    window.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [920, 1280])
def test_logs_share_one_canvas_for_table_and_pagination_without_technical_footer(app, theme, width):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QLabel
    from codexio.theme import theme_colors
    window = Dashboard(AppSettings(), {"theme": theme}, {})
    window.resize(width, 660)
    window.apply_data({"records": records()})
    window.open_page("logs")
    app.processEvents()
    canvas = window._log_canvas
    assert canvas.property("card") and canvas.isAncestorOf(window._log_table)
    assert canvas.isAncestorOf(window._log_pagination)
    assert not hasattr(window, "_request_filter_hint")
    assert window._log_pagination.layout().contentsMargins().left() == 0
    assert window._log_pagination.layout().contentsMargins().right() == 18
    assert abs(window._log_pagination.width() - window._log_drawer.width()) <= 1
    assert window._log_table.geometry().right() < window._log_drawer.inspector.geometry().left()
    image = window.grab().toImage()
    surface = theme_colors(theme)["surface"].lower()
    for widget, point in ((canvas, QPoint(canvas.width() // 2, 10)),
                          (window._log_table.viewport(), QPoint(20, window._log_table.viewport().height() - 8))):
        position = widget.mapTo(window, point)
        assert image.pixelColor(position).name() == surface
    for mode in ("model_call", "user_request"):
        window._log_mode.setCurrentIndex(window._log_mode.findData(mode))
        assert not any(label.text().startswith(("按 Session", "每行使用该次调用")) for label in window._pages["logs"].findChildren(QLabel))
    window.close()


def test_source_column_defaults_hidden_and_saved_setting_applies_to_both_modes(app, tmp_path):
    path = tmp_path / "analytics.json"
    config = load_analytics_config(path)
    assert config["show_log_source"] is False
    window = Dashboard(AppSettings(), config, {"config": lambda value: save_analytics_config(value, path)})
    window.resize(1600, 850)
    window.apply_data({"records": records()})
    window.open_page("logs")
    app.processEvents()
    table = window._log_table
    assert table.isColumnHidden(7) and not table.isColumnHidden(6)
    initial_width = table.columnWidth(0)
    assert sum(table.columnWidth(i) for i in range(table.columnCount())) == table.viewport().width()
    window._log_mode.setCurrentIndex(window._log_mode.findData("model_call"))
    assert table.isColumnHidden(6) and not table.isColumnHidden(5)
    window._log_mode.setCurrentIndex(window._log_mode.findData("user_request"))
    assert table.isColumnHidden(7) and not table.isColumnHidden(6)
    window.open_page("settings")
    assert not window._show_log_source.isChecked()
    window._show_log_source.setChecked(True)
    assert table.isColumnHidden(7)
    window._save_settings()
    window.open_page("logs")
    app.processEvents()
    assert not table.isColumnHidden(7) and table.columnWidth(0) < initial_width
    assert load_analytics_config(path)["show_log_source"] is True
    window._log_mode.setCurrentIndex(window._log_mode.findData("model_call"))
    assert not table.isColumnHidden(6)
    window.close()
    reopened = Dashboard(AppSettings(), load_analytics_config(path), {})
    reopened.open_page("logs")
    assert not reopened._log_table.isColumnHidden(7)
    reopened.close()


def test_source_visibility_draft_survives_window_recreation_without_saving(app):
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("settings")
    window._show_log_source.setChecked(True)
    state = window.capture_view_state()
    assert "show_log_source" not in window._config
    window.close()
    restored = Dashboard(AppSettings(), {}, {})
    restored.restore_view_state(state)
    restored.open_page("settings")
    assert restored._show_log_source.isChecked()
    restored.open_page("logs")
    assert restored._log_table.isColumnHidden(7)
    restored.close()
