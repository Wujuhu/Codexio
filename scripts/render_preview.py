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
    from PySide6.QtWidgets import QApplication, QMenu
    from PySide6.QtCore import QPoint
    from aiquota.analytics_config import default_config
    from aiquota.dashboard import Dashboard, RequestDetails, SessionTooltip
    from aiquota.pricing import PricingCatalog
    from aiquota.rate_limits import QuotaStatus, parse_rate_limits_result, quota_state_from_snapshot
    from aiquota.settings import AppSettings
    from aiquota.theme import apply_theme, apply_dark_menu
    from aiquota.usage_store import UsageStore
    from aiquota.usage_worker import UsageWorker
    from aiquota.window import QuotaWindow

    app = QApplication.instance() or QApplication([])
    out = ROOT / "build" / "preview"
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aiquota-preview-") as temp:
        os.environ["LOCALAPPDATA"] = temp
        config = default_config()
        config["theme"] = "dark"
        settings = AppSettings()
        window = Dashboard(settings, config, {})
        window.resize(1200, 820)
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
        now = int(time.time())
        state = quota_state_from_snapshot(parse_rate_limits_result({"rateLimits": {
            "planType": "pro", "primary": {"usedPercent": 28, "windowDurationMins": 300, "resetsAt": now + 3600},
            "secondary": {"usedPercent": 38, "windowDurationMins": 10080, "resetsAt": now + 86400 * 3},
        }, "rateLimitResetCredits": {"availableCount": 2}}), status=QuotaStatus.OK, message="模拟数据")
        window.apply_quota(state)
        widget.apply_state(state)
        widget.apply_usage_summary(data["summaries"]["today"])
        for theme in ("dark", "light"):
            window.config_updated(dict(config, theme=theme))
            widget.apply_theme(theme)
            for page in ("overview", "logs", "trends", "pricing", "settings"):
                window.open_page(page)
                app.processEvents()
                window.grab().save(str(out / (page + "-" + theme + ".png")))
                if page == "overview":
                    area = window._stack.widget(0)
                    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
                    app.processEvents()
                    window.grab().save(str(out / ("overview-latest-" + theme + ".png")))
                    area.verticalScrollBar().setValue(0)
                if page == "trends":
                    window._trend_model.setCurrentIndex(1)
                    app.processEvents()
                    window.grab().save(str(out / ("trends-model-" + theme + ".png")))
                    window._trend_model.setCurrentIndex(0)
                    chart = window._trend_chart
                    bucket = next(bucket for bucket in chart.buckets if bucket["requests"])
                    chart._tooltip.show_at(QPoint(20, 20), chart._tooltip_text(bucket), theme)
                    app.processEvents()
                    chart._tooltip.grab().save(str(out / ("chart-tooltip-" + theme + ".png")))
                    chart._tooltip.hide()
                    original = set(chart._enabled)
                    chart._enabled = {key for key, _, _ in chart.SERIES}
                    chart._tooltip.show_at(QPoint(20, 20), chart._tooltip_text(bucket), theme)
                    app.processEvents()
                    chart._tooltip.grab().save(str(out / ("chart-tooltip-detail-" + theme + ".png")))
                    chart._tooltip.hide()
                    chart._enabled = original
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
        from aiquota.usage_queries import UsageQueries
        sample_record = UsageQueries(data["query_path"]).page(mode="model_call", page_size=1)["rows"][0]
        details = RequestDetails(sample_record, "dark", window)
        for edge in ("top", "left"):
            widget._apply_dock(edge, persist=False)
            app.processEvents()
            widget.grab().save(str(out / ("widget-docked-" + edge + ".png")))
        widget._apply_dock("none", persist=False)
        widget.apply_visual_style("orb", persist=False, reset_size=True)
        widget.apply_usage_summary(data["summaries"]["today"])
        app.processEvents()
        widget.grab().save(str(out / "widget-orb.png"))
        details.show()
        app.processEvents()
        details.grab().save(str(out / "request-details.png"))
        tip = SessionTooltip(sample_record["session_title"], sample_record["prompt_preview"],
                             output_preview=sample_record.get("output_preview", ""))
        apply_theme(tip, "dark")
        tip.show()
        app.processEvents()
        tip.grab().save(str(out / "session-tooltip.png"))
        tip.close()
        details.close()
        widget.close()
        window.hide()
        app.processEvents()
    print("Synthetic previews:", out)


if __name__ == "__main__":
    main()
