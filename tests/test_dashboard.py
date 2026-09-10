from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from codexio.charts import bucket_records
from codexio.dashboard import Dashboard, HistoryAssignmentEditor, PAGE_NAMES, PriceEditor, RequestDetails, SessionTooltip, TwoLineText
from codexio.settings import AppSettings
from codexio.theme import apply_theme, ensure_ui_fonts


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    ensure_ui_fonts()
    return instance


def record(index=0, **updates):
    value = dict(id=str(index), response_id="resp-" + str(index), session_id="session-1234567890",
                 turn_id="turn-" + str(index), timestamp=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
                 model="gpt-6-astra", service_tier="default", input_tokens=1000, cached_input_tokens=800,
                 cache_write_input_tokens=50, output_tokens=100, total_tokens=1100, cost_usd=0.04,
                 pricing_status="priced", source_id="local", source_name="本机", session_title="安全的会话标题",
                 prompt_preview="请检查这个请求的 Token 计量。")
    value.update(updates)
    return value


def test_tooltip_is_plain_text_and_hard_limited_to_three_visual_lines(app):
    payload = "<img src='https://invalid.example/a'>中文标点。" * 40
    tooltip = SessionTooltip("<b>这不是富文本</b>", payload)
    apply_theme(tooltip, "dark")
    tooltip.show_at(QPoint(20, 20))
    app.processEvents()
    preview = tooltip.preview
    layout, lines = preview.layout_lines(350)
    assert len(lines) == 3
    assert preview.text.startswith("用户：<img")
    assert lines[-1].textStart() + lines[-1].textLength() < len(preview.text)
    image = tooltip.grab().toImage()
    assert not image.isNull()
    assert preview.last_line_count == 3
    assert preview.height() >= sum(line.height() for line in lines)
    tooltip.close()


def test_tooltip_long_unbroken_input_and_short_input(app):
    text = TwoLineText("A" * 5000)
    assert len(text.layout_lines(250)[1]) == 2
    short = TwoLineText("单行输入")
    assert len(short.layout_lines(350)[1]) == 1
    assert short.text == "单行输入"
    emoji = TwoLineText("🧪科研测试" * 80)
    emoji.resize(300, 50)
    emoji.show()
    app.processEvents()
    assert len(emoji.layout_lines(300)[1]) == 2
    assert not emoji.grab().isNull()
    emoji.close()


def test_request_filters_and_pagination(app):
    dashboard = Dashboard(AppSettings(), {"theme": "light"}, {})
    dashboard.open_page("logs")
    rows = [record(i) for i in range(220)] + [record(300, model="other", source_id="ssh:test", service_tier="priority")]
    dashboard.apply_data({"records": rows})
    assert dashboard._log_table.rowCount() == 100
    assert len(dashboard._filtered_records) == 221
    dashboard._change_page(2)
    assert dashboard._log_table.rowCount() == 21
    dashboard._log_model.setCurrentIndex(dashboard._log_model.findData("other"))
    assert dashboard._page_number == 0
    assert dashboard._log_table.rowCount() == 1
    dashboard._log_tier.setCurrentIndex(dashboard._log_tier.findData("default"))
    assert dashboard._log_table.rowCount() == 0
    dashboard.deleteLater()


def test_saving_ui_settings_preserves_window_geometry(app):
    changes = []
    original = AppSettings(window_x=57, window_y=81, window_width=349, dock_side_width=91)
    dashboard = Dashboard(original, {"theme": "system", "ssh_sources": []}, {"settings": changes.append})
    dashboard.open_page("settings")
    dashboard._setting_widgets["background_opacity"].setValue(40)
    dashboard._save_settings()
    assert changes[0].background_opacity == 40
    assert (changes[0].window_x, changes[0].window_y, changes[0].window_width, changes[0].dock_side_width) == (57, 81, 349, 91)
    dashboard.deleteLater()


def test_history_assignment_requires_explicit_confirmation_and_half_open_range(app):
    dialog = HistoryAssignmentEditor([record()], [{"id": "local", "name": "本机"}])
    dialog._save()
    assert dialog.result() == 0
    dialog.confirm.setChecked(True)
    data = dialog.result_value()
    assert data["source_id"] == "local"
    assert data["account_key"] == "current"
    assert datetime.fromisoformat(data["end"]) > datetime.fromisoformat(data["start"])
    dialog.deleteLater()


def test_chart_preserves_unpriced_and_separates_cache_categories():
    now = datetime.now().astimezone()
    rows = [record(), record(1, cost_usd=None)]
    buckets = bucket_records(rows, "today", "hour", now=now)
    assert sum(b["input"] for b in buckets) == 300
    assert sum(b["cache_read"] for b in buckets) == 1600
    assert sum(b["cache_write"] for b in buckets) == 100
    assert sum(b["unpriced"] for b in buckets) == 1
    assert sum(b["usd"] for b in buckets) == 0.04


def test_latest_week_estimate_uses_newest_first_and_real_estimator_fields(app):
    now = datetime.now(timezone.utc)
    current = dict(limit_id="codex", account_key="current", reset_at=(now+timedelta(days=3)).timestamp(),
                   start=(now-timedelta(hours=3)).isoformat(), end=(now-timedelta(hours=1)).isoformat())
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("subscription")
    dashboard.apply_data({"weekly_estimates": [
        dict(current, estimated_total_usd=1200.0, estimated_remaining_usd=600.0, delta_percent=20, plan_type="pro", reason="观测区间"),
        {"estimated_total_usd": 900.0, "plan_type": "plus"},
    ]})
    assert dashboard._estimate_value.text() == "$1,200.00"
    assert "600" in dashboard._estimate_period.text()
    assert "本地观测参考" in dashboard._estimate_note.text()


def test_subscription_displays_server_range_and_separate_local_reference(app):
    now = datetime.now(timezone.utc)
    reset = (now+timedelta(days=2)).timestamp()
    local = dict(estimated_total_usd=999, estimated_remaining_usd=400, delta_percent=20, plan_type="pro",
                 limit_id="codex", account_key="current", reset_at=reset, start=(now-timedelta(days=3)).isoformat(),
                 end=(now-timedelta(days=2)).isoformat())
    server = dict(local, account_key="verified", method="server_range", status="ready", estimated_total_usd=None,
                  low_usd=980.39, high_usd=1428.57, usd_per_credit=0.04, delta_percent=50)
    data = dict(weekly_estimates=[local], weekly_server_estimates=[server], server_usage_context=dict(
        account_key="verified", quota_status="ok", daily_status="ok", last_quota_at=now.isoformat(),
        last_daily_at=now.isoformat(), reset_at=reset, plan_type="pro"))
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("subscription")
    dashboard.apply_data(data)
    assert dashboard._estimate_value.text() == "$980.39 – $1,428.57"
    assert "全账号估算范围" in dashboard._estimate_note.text()
    assert dashboard._estimate_reference.text() == "本地观测参考：$999.00"
    view = dashboard._subscription_history
    assert view.rowCount() == 2 and view.columnCount() == 7
    assert view.item(0, 5).text() == "服务端 · 跨多日"
    assert view.item(1, 5).text() == "本地观测参考"
    assert "$0.04" in view.item(0, 3).toolTip()
    dashboard.close()


def test_server_setting_has_no_credit_conversion_control(app):
    from codexio.analytics_config import default_config
    changes = []
    dashboard = Dashboard(AppSettings(), default_config(), {"config": changes.append})
    dashboard.open_page("settings")
    assert not hasattr(dashboard, "_credit_rate")
    dashboard._server_estimates_enabled.setChecked(False)
    dashboard._save_settings()
    assert "usd_per_credit" not in changes[-1]
    assert changes[-1]["server_estimates_enabled"] is False
    dashboard.close()
    dashboard.deleteLater()


def test_request_details_keeps_literal_ids_and_does_not_invent_latency(app):
    detail = RequestDetails(record(session_id="<b>literal-session</b>"))
    assert detail.session_id.text() == "<b>literal-session</b>"
    assert detail.session_id.isReadOnly()
    detail.deleteLater()


def test_manual_price_can_add_previously_unknown_model(app, tmp_path):
    from codexio.pricing import PricingCatalog
    editor = PriceEditor({})
    editor.model.setText("not-yet-in-catalog")
    for key, value in dict(input=5, cache_read=0.5, cache_write=6.25, output=20).items():
        editor.inputs[key].setValue(value)
    catalog = PricingCatalog(tmp_path)
    catalog.set_override(editor.model.text(), editor.rates())
    rows = [r for r in catalog.rows() if r["model"] == "not-yet-in-catalog"]
    assert len(rows) == 1
    assert rows[0]["threshold"] == 0
    assert rows[0]["input"] == 5
    editor.deleteLater()


def test_open_page_reopens_hidden_main_window(app):
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.hide()
    dashboard.open_page("usage", "week")
    app.processEvents()
    assert dashboard.isVisible()
    assert dashboard._stack.currentIndex() == PAGE_NAMES.index("trends")
    assert dashboard._trend_period.currentData() == "week"
    dashboard.hide()
    dashboard.deleteLater()


def test_trend_period_defaults_and_custom_date_filter(app):
    from PySide6.QtCore import QDate
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("trends")
    now = datetime.now().astimezone()
    yesterday = now - timedelta(days=1)
    dashboard.apply_data({"records": [record(), record(1, timestamp=yesterday.isoformat())]})
    for period, expected in (("week", "day"), ("month", "day"), ("all", "week"), ("today", "hour")):
        dashboard._trend_period.setCurrentIndex(dashboard._trend_period.findData(period))
        assert dashboard._granularity.currentData() == expected
    dashboard._trend_period.setCurrentIndex(dashboard._trend_period.findData("custom"))
    date = QDate(yesterday.year, yesterday.month, yesterday.day)
    dashboard._trend_start.setDate(date)
    dashboard._trend_end.setDate(date)
    assert sum(b["requests"] for b in dashboard._trend_chart.buckets) == 1
    dashboard._trend_start.setDate(date.addDays(1))
    assert dashboard._trend_chart.buckets == []
    assert "起始日期" in dashboard._trend_note.text()
    dashboard.deleteLater()


def test_prices_only_show_available_codex_models_and_two_decimal_rates(app):
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("pricing")
    source = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    dashboard.apply_data({"available_models": ["gpt-6-astra", "gpt-5.3-codex-spark"], "prices": [
        dict(model="gpt-6-astra", input=10, cache_read=1.125, cache_write=12.5, output=50, source=source),
        dict(model="not-offered-by-codex", input=1, output=2)]})
    assert dashboard._price_table.columnCount() == 6
    assert dashboard._price_table.rowCount() == 2
    assert dashboard._price_table.item(0, 2).text() == "10.00"
    assert dashboard._price_table.item(0, 3).text() == "1.13"
    assert dashboard._price_table.item(1, 0).text() == "gpt-5.3-codex-spark"
    assert dashboard._price_table.item(1, 2).text() == "未定价"
    dashboard.deleteLater()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_price_headers_share_alignment_and_numeric_column_space(app, theme):
    dashboard = Dashboard(AppSettings(), {"theme": theme}, {})
    dashboard.resize(1180, 800)
    dashboard.apply_data({"available_models": ["gpt-6-astra"], "prices": [
        dict(model="gpt-6-astra", input=10, cache_read=1, cache_write=12.5, output=50)]})
    dashboard.open_page("pricing")
    app.processEvents()
    view = dashboard._price_table
    assert not view.horizontalHeader().stretchLastSection()
    for column in range(6):
        expected = Qt.AlignmentFlag.AlignCenter
        assert view.horizontalHeaderItem(column).textAlignment() == expected
        assert view.item(0, column).textAlignment() == expected
        assert view.visualRect(view.model().index(0, column)).left() == view.horizontalHeader().sectionViewportPosition(column)
    widths = [view.columnWidth(column) for column in range(6)]
    assert max(widths) - min(widths) <= 1
    dashboard.hide()
    dashboard.deleteLater()


def test_latest_request_is_embedded_and_home_amount_is_larger(app):
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("overview", "today")
    data = record(cost_usd=80.01)
    dashboard.apply_data({"records": [data], "summaries": {"today": {"tokens": 1100, "usd": 80.01}}})
    app.processEvents()
    assert dashboard._recent_table.rowCount() == 1
    assert data["prompt_preview"] in dashboard._recent_table.item(0, 0).text()
    assert not dashboard._recent_table.isWindow()
    assert dashboard._overview_cost.text() == "$80.01"
    assert dashboard._overview_cost.font().pixelSize() >= 20
    dashboard.apply_data({"records": [record(2, session_id="new-session")]})
    assert dashboard._latest_record["session_id"] == "new-session"
    dashboard.deleteLater()


def test_trends_open_to_today_and_model_column_has_no_tier(app):
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("trends")
    assert dashboard._trend_period.currentData() == "today"
    assert dashboard._granularity.currentData() == "hour"
    dashboard.apply_data({"records": [record(service_tier="priority")]})
    dashboard.open_page("logs")
    assert dashboard._log_table.horizontalHeaderItem(1).text() == "模型"
    assert dashboard._log_table.item(0, 1).text().splitlines()[0] == "gpt-6-astra"
    assert dashboard._log_table.columnWidth(1) < 185
    assert dashboard._log_table.horizontalHeaderItem(2).text() == "档位"
    assert dashboard._log_table.item(0, 2).text() == "Fast"
    dashboard.deleteLater()


@pytest.mark.parametrize("tier,expected", [("priority", "Fast"), ("fast", "Fast"), ("default", "Standard"),
                                           ("standard", "Standard"), (None, "未记录"), ("auto", "未记录"), ("flex", "未记录")])
def test_fast_column_never_infers_missing_tier(tier, expected):
    from codexio.dashboard import fast_mode_label
    assert fast_mode_label(record(service_tier=tier)) == expected


def test_session_title_and_two_three_line_messages(app):
    tip = SessionTooltip("完整会话标题包含较长的项目需求说明" * 12, "用户消息" * 60, output_preview="模型可见输出" * 60)
    apply_theme(tip, "dark")
    tip.show_at(QPoint(20, 20))
    app.processEvents()
    assert len(tip.title.layout_lines(tip.title.width())[1]) == 2
    assert len(tip.preview.layout_lines(tip.preview.width())[1]) == 3
    assert len(tip.output_preview.layout_lines(tip.output_preview.width())[1]) == 3
    assert tip.preview.text.startswith("用户：用户消息")
    assert tip.output_preview.text.startswith("模型：模型可见输出")
    assert tip.width() < 440
    assert tip.height() > 180
    tip.close()


def test_home_reset_card_times_and_minimal_request(app):
    from PySide6.QtWidgets import QLabel
    from codexio.rate_limits import QuotaStatus, parse_rate_limits_result, quota_state_from_snapshot
    import time
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("trends")
    data = record()
    dashboard.apply_data({"records": [data], "updated_at": data["timestamp"]})
    assert dashboard._footer.text().startswith("更新时间 ")
    assert "API" not in dashboard._footer.text()
    assert dashboard._trend_note.isHidden()
    dashboard.open_page("overview")
    assert dashboard._recent_table.rowCount() == 1
    assert dashboard._overview_chart._compact
    state = quota_state_from_snapshot(parse_rate_limits_result({"rateLimits": {"primary": {
        "usedPercent": 20, "windowDurationMins": 10080, "resetsAt": int(time.time()) + 3600}},
        "rateLimitResetCredits": {"availableCount": 3}}), status=QuotaStatus.OK, message="")
    dashboard.apply_quota(state)
    assert dashboard._quota_widgets["overview"]["week"].remaining == 80
    reset = dashboard._quota_widgets["overview"]["week"].reset_text
    assert "今天" in reset or "明天" in reset
    dashboard.open_page("subscription")
    assert dashboard._reset_count.text() == "3 次可用"
    dashboard.deleteLater()


def test_log_short_columns_are_compact_and_preview_icon_stays_visible(app):
    from PySide6.QtWidgets import QLineEdit
    dashboard = Dashboard(AppSettings(), {"show_log_source": True}, {})
    dashboard.apply_data({"records": [record(session_id="01234567-89ab-cdef-0123-456789abcdef")]})
    dashboard.resize(1100, 800)
    dashboard.open_page("logs")
    app.processEvents()
    widths = [dashboard._log_table.columnWidth(i) for i in range(8)]
    assert widths[2] < widths[1] and widths[6] < widths[7]
    assert dashboard._log_table.horizontalHeaderItem(6).text() == "状态"
    assert dashboard._log_table.horizontalHeaderItem(7).text() == "来源"
    dashboard._inspect_log_row(0)
    fields = dashboard._inspector_scroll.findChildren(QLineEdit)
    assert any(field.text() == "01234567-89ab-cdef-0123-456789abcdef" and field.isReadOnly() for field in fields)
    dashboard.hide()
    dashboard.deleteLater()


def test_trend_filters_only_offered_models_and_preserves_selection(app):
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("trends")
    records = [record(1), record(2, model="gpt-5.6-sol", total_tokens=2200, input_tokens=2100),
               record(3, model="internal-model", total_tokens=3300, input_tokens=3200)]
    data = {"records": records, "available_models": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.3-codex-spark"]}
    dashboard.apply_data(data)
    choices = [dashboard._trend_model.itemData(i) for i in range(dashboard._trend_model.count())]
    assert choices == ["", "gpt-6-astra", "gpt-5.6-sol", "gpt-5.3-codex-spark"]
    dashboard._trend_model.setCurrentIndex(2)
    assert sum(bucket["tokens"] for bucket in dashboard._trend_chart.buckets) == 2200
    dashboard.apply_data(data)
    assert dashboard._trend_model.currentData() == "gpt-5.6-sol"
    assert sum(bucket["requests"] for bucket in dashboard._trend_chart.buckets) == 1
    dashboard._trend_model.setCurrentIndex(3)
    assert not dashboard._trend_chart.buckets
    dashboard.apply_data(dict(data, available_models=["gpt-6-astra"]))
    assert dashboard._trend_model.currentData() == ""
    assert sum(bucket["requests"] for bucket in dashboard._trend_chart.buckets) == 3
    dashboard.deleteLater()


@pytest.mark.parametrize("quota_first", [True, False])
def test_initial_progress_waits_for_both_loads_then_stays_hidden(app, quota_first):
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("overview")
    assert not dashboard._startup_banner.isHidden()
    assert dashboard._startup_progress.minimum() == dashboard._startup_progress.maximum() == 0
    dashboard.set_usage_loading({"loading": True, "stage": "正在扫描本机日志"})
    if quota_first:
        dashboard.apply_quota({"status": "ok"})
    dashboard.apply_data({"records": []})
    assert not dashboard._startup_banner.isHidden()
    dashboard.set_usage_loading({"loading": False, "stage": "加载完成"})
    if not quota_first:
        assert not dashboard._startup_banner.isHidden()
        dashboard.apply_quota({"status": "stale"})
        assert not dashboard._startup_banner.isHidden()
        dashboard.apply_quota({"status": "error", "message": "离线"})
    assert dashboard._startup_banner.isHidden()
    dashboard.set_usage_loading({"loading": True, "stage": "后续刷新"})
    assert dashboard._startup_banner.isHidden()
    dashboard.deleteLater()


def test_initial_progress_failure_is_visible_and_does_not_spin_forever(app):
    dashboard = Dashboard(AppSettings(), {}, {})
    dashboard.open_page("overview")
    dashboard.apply_quota({"status": "error", "message": "额度读取失败"})
    dashboard.set_usage_loading({"loading": False, "error": "用量加载失败"})
    assert dashboard._startup_banner.isHidden()
    assert dashboard._sidebar_status.text() == "用量加载失败"
    assert dashboard._recent_empty.text() == "请求记录加载失败"
    dashboard.deleteLater()
