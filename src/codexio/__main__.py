from __future__ import annotations

from codexio.i18n import tr

import argparse
import atexit
import copy
import sys

if __name__ == "__main__" and sys.platform == "darwin":
    from codexio.native_launcher import launch
    launch(sys.argv[1:])

# This copied helper must stay alive after the Qt application exits or updates.
if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--upstream-proxy":
    from codexio.upstream_proxy import run_helper
    sys.exit(run_helper(sys.argv[2]))

# The copied updater executable must run independently of the Qt application.
if __name__ == "__main__" and sys.platform == "win32" and len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
    from codexio.update_installer import run_update_job
    sys.exit(run_update_job(sys.argv[2]))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMenu

from codexio.logging_setup import get_logger, setup_logging
from codexio.settings import load_settings
from codexio.visuals import display_font, register_bundled_fonts
from codexio.app_icon import load_app_icon
from codexio.worker import QuotaWorker
from codexio.analytics_config import load_analytics_config, save_analytics_config
from codexio.usage_worker import UsageWorker
from codexio.analytics_client import AccountReportsWorker
from codexio.dashboard_host import DashboardHost
from codexio.appearance_icons import app_icon
from codexio.usage_report_ui import UsageReportController
from codexio.mobile_windows import MobileWindowsHost
from codexio.settings import data_dir
from codexio.upstream_manager import UpstreamManager


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.smoke_test:
        from pathlib import Path
        Path(args.smoke_test).mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        from codexio.native_launcher import launch
        launch(list(sys.argv[1:] if argv is None else argv))
    from codexio.tray import TrayController
    from codexio.window import QuotaWindow
    from codexio.update_manager import UpdateManager
    from codexio.update_installer import acknowledge_update
    if args.mock:
        import os
        import tempfile
        from pathlib import Path
        mock_root = Path(tempfile.mkdtemp(prefix="codexio-mock-", dir=str(Path(args.smoke_test).resolve()) if args.smoke_test else None))
        os.environ["CODEXIO_DATA_DIR"] = str(mock_root / "data")
        os.environ["CODEX_HOME"] = str(mock_root / "codex")
        for key in ("CODEXIO_UPDATE_JOB", "CODEXIO_UPSTREAM_JOB"):
            os.environ.pop(key, None)
    setup_logging()
    logger = get_logger("main")
    settings = load_settings()
    analytics_config = load_analytics_config()
    if settings.display_mode == "tray":
        analytics_config["widget_visible"] = False
        settings.display_mode = "top"
    save_analytics_config(analytics_config)
    logger.info(tr("启动 Codexio"))

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Codexio")
    app.setApplicationDisplayName("Codexio")
    app.setQuitOnLastWindowClosed(False)
    app.setFont(display_font(13))
    app.setWindowIcon(app_icon(analytics_config.get("app_icon", "main")))
    mobile_host = MobileWindowsHost(app, mock=args.mock)

    worker = QuotaWorker(settings, mock=args.mock)
    usage = UsageWorker(analytics_config, mock=args.mock)
    reports = AccountReportsWorker(mock=args.mock)
    closing = False
    updater = None
    upstream = None

    def cleanup() -> None:
        nonlocal closing
        if closing:
            return
        closing = True
        if upstream is not None:
            upstream.stop()
        if updater is not None:
            updater.stop()
        mobile_host.close()
        dashboard_host.save_geometry()
        window.persist_settings()
        worker.stop()
        reports.stop()
        usage.stop()

    def quit_app() -> None:
        if upstream is not None:
            upstream.request_quit(finish_quit)
        else:
            finish_quit()

    def finish_quit() -> None:
        logger.info(tr("退出 Codexio"))
        cleanup()
        # An explicit exit must bypass the main window's close-to-tray veto.
        app.exit(0)

    def refresh() -> None:
        worker.request_refresh()
        usage.request_refresh()

    def open_main(page: str = "overview", period: str | None = None) -> None:
        dashboard = dashboard_host.open(page, period)
        def present_pending():
            if updater is not None:
                updater.present_if_possible()
            usage_reports.opened(dashboard, dashboard_host._data)
        QTimer.singleShot(0, present_pending)
        return dashboard

    def save_report_style(style: str) -> None:
        analytics_config["usage_report_style"] = style
        save_analytics_config(analytics_config)

    def toggle_mobile(enabled: bool) -> None:
        analytics_config["mobile_sync_enabled"] = bool(enabled)
        save_analytics_config(analytics_config)
        if enabled:
            mobile_host.start()
            mobile_host.update(dashboard_host._data, dashboard_host._quota)
        else:
            mobile_host.stop(disable=True)

    def apply_app_icon(style: str) -> None:
        analytics_config["app_icon"] = style
        save_analytics_config(analytics_config)
        app.setWindowIcon(app_icon(style))
        if dashboard_host.dashboard is not None:
            dashboard_host.dashboard.setWindowIcon(app.windowIcon())
        dashboard_host.config_updated(analytics_config)

    def apply_config(config: dict) -> None:
        nonlocal analytics_config
        analytics_config = copy.deepcopy(config)
        save_analytics_config(analytics_config)
        if updater is not None:
            updater.set_enabled(bool(analytics_config.get("auto_update", True)))
        usage.update_config(analytics_config)
        dashboard_host.config_updated(analytics_config)
        window.apply_theme("dark")
        window.setVisible(bool(analytics_config.get("widget_visible", True)))
        widget_action.setChecked(window.isVisible())

    def toggle_widget(visible: bool) -> None:
        config = dict(analytics_config, widget_visible=bool(visible))
        apply_config(config)

    def apply_settings(value) -> None:
        if value.display_mode == "tray":
            value.display_mode = "top"
            toggle_widget(False)
        window.apply_settings(value)
        if not analytics_config.get("widget_visible"):
            window.hide()

    def save_main_geometry(geometry: str) -> None:
        analytics_config["main_geometry"] = geometry
        save_analytics_config(analytics_config)

    window = QuotaWindow(
        settings,
        on_refresh=refresh,
        on_interval_changed=worker.set_interval,
        on_quit=quit_app,
        on_settings_applied=worker.update_settings,
        on_open=open_main,
        on_hide=lambda: toggle_widget(False),
        start_hidden=True,
    )
    window.persist_settings()
    dashboard_host = DashboardHost(window.current_settings, lambda: analytics_config, {
        "refresh": refresh, "settings": apply_settings, "config": apply_config,
        "sync_prices": usage.request_sync, "price_override": usage.set_price_override,
        "toggle_widget": toggle_widget, "quit": quit_app, "rescan": usage.rescan,
        "estimates_visible": usage.set_estimates_visible,
        "consume_reset": worker.request_reset,
        "account_reports": lambda force=False: reports.request_reports((dashboard_host._data or {}).get("local_threads", []), force) if worker._applicable else None,
        "main_hidden": save_main_geometry,
        "check_update": lambda: updater.check() if updater is not None else None,
        "upstream_toggle": lambda value: upstream.toggle(value),
        "open_usage_report": lambda: usage_reports.open_manual(dashboard_host.dashboard, dashboard_host._data),
        "main_activated": lambda: (updater.present_if_possible() if updater is not None else None,
                                    usage_reports.data_available(dashboard_host.dashboard, dashboard_host._data)),
        "app_icon": apply_app_icon,
        "mobile_host": mobile_host, "mobile_toggle": toggle_mobile,
    }, app)
    usage_reports = UsageReportController(app, lambda: analytics_config, save_report_style,
                                          can_present=lambda: updater is None or not updater.is_resolving_startup,
                                          mock=args.mock)
    upstream = UpstreamManager(app, data_dir(), lambda: analytics_config, apply_config,
                               lambda: dashboard_host.dashboard, mock=args.mock)
    upstream.on_quit = finish_quit
    upstream.status_changed.connect(dashboard_host.set_upstream_status)
    upstream.observations_changed.connect(dashboard_host.refresh_upstream)
    menu = QMenu()
    menu.addAction(tr("打开主界面")).triggered.connect(lambda: open_main())
    widget_action = menu.addAction(tr("显示悬浮窗"))
    widget_action.setCheckable(True)
    widget_action.setChecked(bool(analytics_config.get("widget_visible", True)))
    widget_action.triggered.connect(toggle_widget)
    close_widget_action = menu.addAction(tr("关闭悬浮窗"))
    close_widget_action.triggered.connect(lambda: toggle_widget(False))
    menu.addSeparator()
    update_action = menu.addAction(tr("检查并更新"))
    update_action.triggered.connect(lambda: updater.check() if updater is not None else None)
    menu.addAction(tr("退出程序")).triggered.connect(quit_app)
    tray = TrayController(
        app,
        menu,
        on_activated=lambda: open_main(),
        icon=load_app_icon(),
    )
    updater = UpdateManager(app, lambda: upstream.quit_for_update(finish_quit), available=False if args.mock else None)
    updater.set_presentation(lambda: dashboard_host.dashboard,
                             lambda: usage_reports.data_available(dashboard_host.dashboard, dashboard_host._data))

    def update_status(message: str, busy: bool) -> None:
        dashboard_host.set_update_status(message, busy)
        update_action.setEnabled(not busy)

    updater.status_changed.connect(update_status)

    def acknowledge_restart() -> None:
        message = acknowledge_update()
        if message:
            update_status(message, False)

    def on_state(state) -> None:
        window.apply_state(state)
        dashboard_host.apply_quota(state)
        tray.update_state(state, show_five=window.shows_five_hour())
        widget_action.setChecked(bool(analytics_config.get("widget_visible", True)))
        window.setVisible(bool(analytics_config.get("widget_visible", True)))
        mobile_host.update(dashboard_host._data, state)

    def on_usage(data) -> None:
        dashboard_host.apply_data(data)
        window.apply_usage_summary(data["summaries"]["today"])
        usage_reports.data_available(dashboard_host.dashboard, data)
        mobile_host.update(data, dashboard_host._quota)

    def update_theme(*_args) -> None:
        window.apply_theme("dark")
        dashboard_host.config_updated(analytics_config)

    worker.reset_result.connect(dashboard_host.set_reset_result)
    worker.identity_changed.connect(lambda: dashboard_host.set_reset_result({"clear": True}))
    worker.identity_changed.connect(reports.invalidate)
    reports.reports_changed.connect(dashboard_host.apply_reports)
    worker.state_changed.connect(on_state)
    worker.snapshot_changed.connect(usage.add_snapshot)
    worker.identity_changed.connect(usage.invalidate_estimate_window)
    worker.applicability_changed.connect(usage.set_quota_applicable)
    usage.data_changed.connect(on_usage)
    usage.loading_changed.connect(dashboard_host.set_usage_loading)
    usage.progress_changed.connect(dashboard_host.set_progress)
    scheme_signal = getattr(app.styleHints(), "colorSchemeChanged", None)
    if scheme_signal is not None:
        scheme_signal.connect(update_theme)
    update_theme()
    window.setVisible(bool(analytics_config.get("widget_visible", True)))
    upstream.startup_ready.connect(lambda *_: dashboard_host.open("overview", "today"))
    usage.start()
    reports.start()
    worker.start()
    updater.start(bool(analytics_config.get("auto_update", True)))
    if analytics_config.get("mobile_sync_enabled"):
        mobile_host.start()
    QTimer.singleShot(0, upstream.start)
    if not args.mock:
        QTimer.singleShot(1500, acknowledge_restart)
    if args.smoke_test:
        from pathlib import Path
        from types import SimpleNamespace
        from codexio.basic_smoke import schedule_smoke_test
        smoke_controller = SimpleNamespace(app=app, dashboard_host=dashboard_host, open_main=open_main, stop=cleanup)
        schedule_smoke_test(smoke_controller, Path(args.smoke_test))
    atexit.register(cleanup)
    app.aboutToQuit.connect(cleanup)
    return app.exec()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=tr("Codexio：本机 Codex 额度与用量统计"))
    parser.add_argument("--mock", action="store_true", help=tr("使用模拟额度数据，不启动 app-server"))
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.smoke_test and not args.mock:
        parser.error("--smoke-test requires --mock")
    return args


if __name__ == "__main__":
    sys.exit(main())
