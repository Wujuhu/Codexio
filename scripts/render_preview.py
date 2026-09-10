"""Render deterministic, synthetic UI previews without accessing real Codex data."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> None:
    from PySide6.QtWidgets import QApplication, QMenu, QWidget
    from PySide6.QtCore import QPoint, QRect
    from codexio.analytics_config import default_config
    from codexio.dashboard import Dashboard, SessionTooltip
    from codexio.pricing import PricingCatalog
    from codexio.rate_limits import QuotaStatus, parse_rate_limits_result, quota_state_from_snapshot
    from codexio.settings import AppSettings
    from codexio.theme import apply_theme, apply_dark_menu
    from codexio.usage_store import UsageStore
    from codexio.usage_worker import UsageWorker
    from codexio.window import QuotaWindow

    app = QApplication.instance() or QApplication([])
    out = Path(os.environ.get("CODEXIO_PREVIEW_DIR", str(ROOT / "build" / "preview")))
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codexio-preview-") as temp:
        os.environ["LOCALAPPDATA"] = temp
        config = default_config()
        config["theme"] = "dark"
        config["subscription_profile"] = {"plan": "Pro 20X", "price_usd": 200, "renewal_date": ""}
        settings = AppSettings()
        window = Dashboard(settings, config, {})
        window.resize(int(os.environ.get("CODEXIO_PREVIEW_WIDTH", "1280")), int(os.environ.get("CODEXIO_PREVIEW_HEIGHT", "850")))
        window.show()
        app.processEvents()
        window.grab().save(str(out / "startup-loading.png"))
        widget = QuotaWindow(settings, lambda: None, lambda _: None, lambda: None)
        worker = UsageWorker(config, mock=True, directory=Path(temp))
        worker._store = UsageStore(Path(temp) / "mock.sqlite")
        worker._catalog = PricingCatalog(Path(temp) / "prices")
        worker._seed_mock()
        captured = []
        worker.data_changed.connect(captured.append)
        worker._publish()
        data = captured[0]
        window.apply_data(data)
        import time
        from datetime import datetime, timezone
        now = int(time.time())
        state = quota_state_from_snapshot(parse_rate_limits_result({"rateLimits": {
            "planType": "pro", "primary": {"usedPercent": 28, "windowDurationMins": 300, "resetsAt": now + 3600},
            "secondary": {"usedPercent": 38, "windowDurationMins": 10080, "resetsAt": now + 86400 * 3},
        }, "rateLimitResetCredits": {"availableCount": 2, "credits": [
            {"id": "demo-first", "status": "available", "resetType": "codexRateLimits", "grantedAt": now - 86400, "expiresAt": now + 86400 * 8},
            {"id": "demo-second", "status": "available", "resetType": "codexRateLimits", "grantedAt": now - 86400, "expiresAt": now + 86400 * 22},
        ]}}), status=QuotaStatus.OK, message="模拟数据", last_success_at=datetime.now(timezone.utc))
        window.apply_quota(state)
        widget.apply_state(state)
        widget.apply_usage_summary(data["summaries"]["today"])
        for theme in ("dark", "light"):
            window.config_updated(dict(config, theme=theme))
            widget.apply_theme(theme)
            for page in ("overview", "subscription", "logs", "trends", "pricing", "settings"):
                window.open_page(page)
                app.processEvents()
                window._sidebar_status.setText("合成数据预览")
                window.grab().save(str(out / (page + "-" + theme + ".png")))
                if page == "settings":
                    window._settings_sections.setCurrentRow(1)
                    app.processEvents()
                    window.grab().save(str(out / ("settings-widget-" + theme + ".png")))
                    window._settings_sections.setCurrentRow(0)
                if page == "subscription":
                    window._pages["subscription"].ensureWidgetVisible(window._subscription_history_section, 0, 12)
                    app.processEvents()
                    window.grab().save(str(out / ("subscription-history-" + theme + ".png")))
                    window._pages["subscription"].verticalScrollBar().setValue(0)
                if page == "logs" and window._filtered_records:
                    window._inspect_log_row(0)
                    app.processEvents()
                    window.grab().save(str(out / ("inspector-" + theme + ".png")))
                    heading = window._inspector_scroll.widget().findChild(QWidget, "inspectorComposedCalls")
                    window._inspector_scroll.ensureWidgetVisible(heading, 0, 110)
                    app.processEvents()
                    window.grab().save(str(out / ("inspector-content-" + theme + ".png")))
                    window._close_inspector()
                if page == "overview":
                    area = window._stack.widget(0)
                    area.ensureWidgetVisible(window._overview_chart, 0, 12)
                    app.processEvents()
                    window.grab().save(str(out / ("overview-chart-" + theme + ".png")))
                    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
                    app.processEvents()
                    window.grab().save(str(out / ("overview-latest-" + theme + ".png")))
                    area.verticalScrollBar().setValue(0)
                if page == "trends":
                    window._trend_model.setCurrentIndex(1)
                    app.processEvents()
                    window.grab().save(str(out / ("trends-model-" + theme + ".png")))
                    window._trend_model.setCurrentIndex(0)
                    area = window._pages["trends"]
                    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
                    app.processEvents()
                    window.grab().save(str(out / ("activity-" + theme + ".png")))
                    activity = window._activity_chart
                    day = next(day for day, row in activity.days.items() if row.get("requests"))
                    activity._tooltip.show_at(QPoint(20, 20), activity.tooltip_text(day), theme)
                    app.processEvents()
                    activity._tooltip.grab().save(str(out / ("activity-tooltip-" + theme + ".png")))
                    activity._tooltip.hide()
                    area.verticalScrollBar().setValue(0)
                    chart = window._trend_chart
                    bucket = next(bucket for bucket in chart.buckets if bucket["requests"])
                    chart._tooltip.show_at(QPoint(20, 20), chart._tooltip_text(bucket), theme)
                    app.processEvents()
                    chart._tooltip.grab().save(str(out / ("chart-tooltip-" + theme + ".png")))
                    chart._tooltip.hide()
                    original = set(chart._enabled)
                    chart._enabled = {key for key, _, _ in chart.SERIES}
                    chart._hover = chart.buckets.index(bucket)
                    chart.update()
                    app.processEvents()
                    window.grab().save(str(out / ("trends-all-series-" + theme + ".png")))
                    window.grab().copy(QRect(chart.mapTo(window, QPoint(0, 0)), chart.size())).save(str(out / ("chart-all-series-" + theme + ".png")))
                    chart._tooltip.show_at(QPoint(20, 20), chart._tooltip_text(bucket), theme)
                    app.processEvents()
                    chart._tooltip.grab().save(str(out / ("chart-tooltip-detail-" + theme + ".png")))
                    chart._tooltip.hide()
                    chart._enabled = original
                    chart._hover = -1
                    window.apply_data({"records": [], "available_models": data.get("available_models", [])})
                    app.processEvents()
                    window.grab().save(str(out / ("trends-empty-" + theme + ".png")))
                    window.apply_data(data)
            widget.grab().save(str(out / ("widget-" + theme + ".png")))
            window._show_estimates()
            app.processEvents()
            dialog = window._dialogs[-1]
            dialog.grab().save(str(out / ("weekly-estimates-" + theme + ".png")))
            dialog.close()
            menu = QMenu(window)
            menu.addAction("打开主界面")
            menu.addAction("显示悬浮窗").setCheckable(True)
            menu.addAction("退出程序")
            apply_dark_menu(menu)
            menu.popup(QPoint(20, 20))
            app.processEvents()
            menu.grab().save(str(out / ("tray-menu-" + theme + ".png")))
            menu.close()
        from codexio.usage_queries import UsageQueries
        sample_record = UsageQueries(data["query_path"]).page(mode="model_call", page_size=1)["rows"][0]
        for edge in ("top", "left"):
            widget._apply_dock(edge, persist=False)
            app.processEvents()
            widget.grab().save(str(out / ("widget-docked-" + edge + ".png")))
        widget._apply_dock("none", persist=False)
        widget.apply_visual_style("orb", persist=False, reset_size=True)
        widget.apply_usage_summary(data["summaries"]["today"])
        app.processEvents()
        widget.grab().save(str(out / "widget-orb.png"))
        tip = SessionTooltip(sample_record["session_title"], sample_record["prompt_preview"],
                             output_preview=sample_record.get("output_preview", ""))
        apply_theme(tip, "dark")
        tip.show()
        app.processEvents()
        tip.grab().save(str(out / "session-tooltip.png"))
        tip.close()
        widget.close()
        window.hide()
        app.processEvents()
    print("Synthetic previews:", out)


if __name__ == "__main__":
    main()
