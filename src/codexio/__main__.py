from __future__ import annotations

import argparse
import atexit
import copy
import sys

# The copied updater executable must run independently of the Qt application.
if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
    from codexio.update_installer import run_update_job
    sys.exit(run_update_job(sys.argv[2]))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMenu

from codexio.logging_setup import get_logger, setup_logging
from codexio.settings import load_settings
from codexio.tray import TrayController
from codexio.visuals import display_font, register_bundled_fonts
from codexio.app_icon import load_app_icon
from codexio.window import QuotaWindow
from codexio.worker import QuotaWorker
from codexio.analytics_config import load_analytics_config, save_analytics_config
from codexio.usage_worker import UsageWorker
from codexio.dashboard_host import DashboardHost
from codexio.update_manager import UpdateManager
from codexio.server_usage_monitor import ServerUsageMonitor
from codexio.settings import data_dir
from codexio.update_installer import acknowledge_update


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    setup_logging()
    logger = get_logger("main")
    settings = load_settings()
    analytics_config = load_analytics_config()
    if settings.display_mode == "tray":
        analytics_config["widget_visible"] = False
        settings.display_mode = "top"
    save_analytics_config(analytics_config)
    logger.info("启动 Codexio")

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Codexio")
    app.setApplicationDisplayName("Codexio")
    app.setQuitOnLastWindowClosed(False)
    family = register_bundled_fonts()
    if not family:
        logger.warning("内嵌字体不可用，界面改用系统字体")
    app.setFont(display_font(13))
    app.setWindowIcon(load_app_icon())

    worker = QuotaWorker(settings, mock=args.mock)
    usage = UsageWorker(analytics_config, mock=args.mock)
    server_usage = ServerUsageMonitor(analytics_config, data_dir(), app, mock=args.mock)
    closing = False
    updater = None

    def cleanup() -> None:
        nonlocal closing
        if closing:
            return
        closing = True
        server_usage.stop()
        if updater is not None:
            updater.stop()
        dashboard_host.save_geometry()
        window.persist_settings()
        worker.stop()
        usage.stop()

    def quit_app() -> None:
        logger.info("退出 Codexio")
        cleanup()
        # An explicit exit must bypass the main window's close-to-tray veto.
        app.exit(0)

    def refresh() -> None:
        worker.request_refresh()
        usage.request_refresh()
        server_usage.request_refresh()

    def open_main(page: str = "overview", period: str | None = None) -> None:
        dashboard_host.open(page, period)

    def apply_config(config: dict) -> None:
        nonlocal analytics_config
        analytics_config = copy.deepcopy(config)
        save_analytics_config(analytics_config)
        if updater is not None:
            updater.set_enabled(bool(analytics_config.get("auto_update", True)))
        usage.update_config(analytics_config)
        server_usage.update_config(analytics_config)
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

    def assign_history(assignment: dict) -> None:
        config = copy.deepcopy(analytics_config)
        config.setdefault("history_assignments", []).append(assignment)
        apply_config(config)

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
    )
    window.persist_settings()
    dashboard_host = DashboardHost(window.current_settings, lambda: analytics_config, {
        "refresh": refresh, "settings": apply_settings, "config": apply_config,
        "sync_prices": usage.request_sync, "price_override": usage.set_price_override,
        "toggle_widget": toggle_widget, "quit": quit_app, "rescan": usage.rescan,
        "assign_history": assign_history,
        "main_hidden": save_main_geometry,
        "check_update": lambda: updater.check() if updater is not None else None,
    }, app)
    menu = QMenu()
    menu.addAction("打开主界面").triggered.connect(lambda: open_main())
    widget_action = menu.addAction("显示悬浮窗")
    widget_action.setCheckable(True)
    widget_action.setChecked(bool(analytics_config.get("widget_visible", True)))
    widget_action.triggered.connect(toggle_widget)
    menu.addAction("关闭悬浮窗").triggered.connect(lambda: toggle_widget(False))
    menu.addSeparator()
    update_action = menu.addAction("检查并更新")
    update_action.triggered.connect(lambda: updater.check() if updater is not None else None)
    menu.addAction("退出程序").triggered.connect(quit_app)
    tray = TrayController(
        app,
        menu,
        on_activated=lambda: open_main(),
        icon=load_app_icon(),
    )
    updater = UpdateManager(app, quit_app, available=False if args.mock else None)

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

    def on_usage(data) -> None:
        dashboard_host.apply_data(data)
        window.apply_usage_summary(data["summaries"]["today"])

    def update_theme(*_args) -> None:
        window.apply_theme("dark")
        dashboard_host.config_updated(analytics_config)

    worker.state_changed.connect(on_state)
    worker.snapshot_changed.connect(usage.add_snapshot)
    usage.data_changed.connect(on_usage)
    usage.loading_changed.connect(dashboard_host.set_usage_loading)
    usage.progress_changed.connect(dashboard_host.set_progress)
    server_usage.updated.connect(usage.request_estimate_refresh)
    scheme_signal = getattr(app.styleHints(), "colorSchemeChanged", None)
    if scheme_signal is not None:
        scheme_signal.connect(update_theme)
    update_theme()
    window.setVisible(bool(analytics_config.get("widget_visible", True)))
    dashboard_host.open("overview", "today")
    usage.start()
    worker.start()
    server_usage.start()
    updater.start(bool(analytics_config.get("auto_update", True)))
    QTimer.singleShot(1500, acknowledge_restart)
    atexit.register(cleanup)
    app.aboutToQuit.connect(cleanup)
    return app.exec()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codexio：本机 Codex 额度与用量统计")
    parser.add_argument("--mock", action="store_true", help="使用模拟额度数据，不启动 app-server")
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
