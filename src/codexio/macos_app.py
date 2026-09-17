"""macOS application lifecycle, independent of the Windows floating widget."""
from __future__ import annotations

import copy
import hashlib
import os
import sys
from functools import partial
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFontDatabase, QKeySequence
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenuBar, QMessageBox

from codexio import __version__
from codexio.analytics_config import load_analytics_config, save_analytics_config
from codexio.app_icon import load_app_icon
from codexio.dashboard import Dashboard
from codexio.dashboard_host import DashboardHost
from codexio.logging_setup import get_logger, setup_logging
from codexio.menu_bar import MenuBarController
from codexio.server_usage_monitor import ServerUsageMonitor
from codexio.settings import data_dir, load_settings, save_settings
from codexio.usage_worker import UsageWorker
from codexio.worker import QuotaWorker


class SingleInstance(QObject):
    """Only the lock owner may remove a stale socket or collect into this store."""
    def __init__(self, directory, on_open, parent=None):
        super().__init__(parent)
        digest = hashlib.sha256(str(directory.resolve()).encode("utf-8")).hexdigest()[:24]
        self.name = "codexio-" + digest
        self.lock = QLockFile(str(directory / "instance.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._connected)
        self._on_open = on_open

    def acquire(self):
        if not self.lock.tryLock(0):
            socket = QLocalSocket(self)
            socket.connectToServer(self.name)
            if not socket.waitForConnected(1500):
                raise RuntimeError("Codexio 已在运行，正在启动或退出；请稍后从菜单栏打开。")
            socket.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            self.lock.unlock()
            raise RuntimeError("无法创建本地应用连接：" + self.server.errorString())
        return True

    def _connected(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
        self._on_open()

    def close(self):
        self.server.close()
        self.lock.unlock()


class MacController(QObject):
    def __init__(self, app, *, mock=False):
        super().__init__(app)
        self.app = app
        self.mock = mock
        self.closing = False
        self.settings = load_settings()
        self.config = load_analytics_config()
        self.config.update(widget_visible=False, auto_update=False)
        save_analytics_config(self.config)
        self.worker = QuotaWorker(self.settings, mock=mock)
        self.usage = UsageWorker(self.config, mock=mock)
        self.server_usage = ServerUsageMonitor(self.config, data_dir(), app, mock=mock)
        self.dashboard_host = DashboardHost(lambda: self.settings, lambda: self.config, {
            "refresh": self.refresh, "settings": self.apply_settings, "config": self.apply_config,
            "sync_prices": self.usage.request_sync, "price_override": self.usage.set_price_override,
            "quit": self.quit, "rescan": self.usage.rescan, "assign_history": self.assign_history,
            "main_hidden": self.save_geometry, "open_data_directory": self.open_data_directory,
        }, app, factory=partial(Dashboard, desktop_platform="macos"))
        self.menu_bar = MenuBarController(app, self.settings, self.config,
                                          on_open=self.open_main, on_refresh=self.refresh, on_quit=self.quit)
        self._build_application_menu()
        self.worker.state_changed.connect(self.on_quota)
        self.worker.snapshot_changed.connect(self.usage.add_snapshot)
        self.usage.data_changed.connect(self.on_usage)
        self.usage.loading_changed.connect(self.on_loading)
        self.usage.progress_changed.connect(self.dashboard_host.set_progress)
        self.server_usage.updated.connect(self.usage.request_estimate_refresh)
        self.app.styleHints().colorSchemeChanged.connect(self.update_theme)
        self.app.aboutToQuit.connect(self.stop)
        # A status-item popup also activates the application on macOS. Window
        # creation must only follow an explicit action, never activation timing.

    def _build_application_menu(self):
        # A parentless bar is the macOS default even after the main window closes.
        self.application_menu = QMenuBar()
        app_menu = self.application_menu.addMenu("Codexio")
        about = app_menu.addAction("关于 Codexio")
        about.setMenuRole(QAction.MenuRole.AboutRole)
        about.triggered.connect(lambda: QMessageBox.about(self.dashboard_host.dashboard, "关于 Codexio",
            "Codexio %s\n\nmacOS 额度与用量面板\n费用按模型定价计算。" % __version__))
        preferences = app_menu.addAction("设置…")
        preferences.setMenuRole(QAction.MenuRole.PreferencesRole)
        preferences.setShortcut(QKeySequence("Ctrl+,"))
        preferences.triggered.connect(lambda: self.open_main("settings"))
        app_menu.addSeparator()
        quit_action = app_menu.addAction("退出 Codexio")
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.quit)
        window_menu = self.application_menu.addMenu("窗口")
        window_menu.addAction("显示主界面", lambda: self.open_main())
        close = window_menu.addAction("关闭窗口", self.close_window)
        close.setShortcut(QKeySequence.StandardKey.Close)
        refresh = window_menu.addAction("刷新数据", self.refresh)
        refresh.setShortcut(QKeySequence("Ctrl+R"))

    def start(self):
        if self.settings.show_main_on_startup or not self.menu_bar.tray.isSystemTrayAvailable():
            self.open_main()
        self.usage.start()
        self.worker.start()
        self.server_usage.start()

    def open_main(self, page="overview", period=None):
        if self.closing:
            return
        self.menu_bar.preview.hide()
        window = self.dashboard_host.open(page, period)
        screen = window.screen() or self.app.primaryScreen()
        if screen:
            rect = screen.availableGeometry().adjusted(12, 12, -12, -12)
            window.resize(max(window.minimumWidth(), min(window.width(), rect.width())),
                          max(window.minimumHeight(), min(window.height(), rect.height())))
            if not rect.contains(window.frameGeometry()):
                window.move(rect.center() - window.rect().center())
        return window

    def close_window(self):
        if self.menu_bar.preview.isVisible():
            self.menu_bar.preview.hide()
        elif self.dashboard_host.dashboard is not None:
            self.dashboard_host.dashboard.close()

    def refresh(self):
        if not self.closing:
            self.worker.request_refresh()
            self.usage.request_refresh()
            self.server_usage.request_refresh()

    def on_quota(self, state):
        self.dashboard_host.apply_quota(state)
        self.menu_bar.preview.apply_quota(state)

    def on_usage(self, data):
        self.dashboard_host.apply_data(data)
        self.menu_bar.preview.apply_data(data)

    def on_loading(self, loading):
        self.dashboard_host.set_usage_loading(loading)
        self.menu_bar.preview.set_usage_loading(loading)

    def apply_settings(self, settings):
        self.settings = settings.normalized()
        save_settings(self.settings)
        self.worker.update_settings(self.settings)
        self.menu_bar.preview.configure(self.settings, self.config)

    def apply_config(self, config):
        self.config = copy.deepcopy(config)
        self.config.update(widget_visible=False, auto_update=False)
        save_analytics_config(self.config)
        self.usage.update_config(self.config)
        self.server_usage.update_config(self.config)
        self.dashboard_host.config_updated(self.config)
        self.menu_bar.preview.configure(self.settings, self.config)

    def assign_history(self, assignment):
        config = copy.deepcopy(self.config)
        config.setdefault("history_assignments", []).append(assignment)
        self.apply_config(config)

    def save_geometry(self, geometry):
        self.config["main_geometry"] = geometry
        save_analytics_config(self.config)

    def open_data_directory(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir())))

    def update_theme(self, *_args):
        self.dashboard_host.config_updated(self.config)
        self.menu_bar.preview.configure(self.settings, self.config)

    def stop(self):
        if self.closing:
            return
        self.closing = True
        self.menu_bar.stop()
        self.dashboard_host.save_geometry()
        save_settings(self.settings)
        self.server_usage.stop()
        self.worker.stop()
        self.usage.stop()

    def quit(self):
        self.stop()
        self.app.exit(0)


def run_macos(args):
    if args.smoke_test:
        os.environ["CODEXIO_DATA_DIR"] = str(Path(args.smoke_test).resolve() / "data")
    elif args.mock and not os.environ.get("CODEXIO_DATA_DIR"):
        os.environ["CODEXIO_DATA_DIR"] = str(data_dir() / "mock")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Codexio")
    app.setApplicationDisplayName("Codexio")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Codexio")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(load_app_icon())
    app.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
    controller = None
    instance = SingleInstance(data_dir(), lambda: controller.open_main() if controller is not None else None, app)
    try:
        if not instance.acquire():
            return 0
    except RuntimeError as exc:
        QMessageBox.information(None, "Codexio", str(exc))
        return 1
    setup_logging()
    get_logger("macos").info("启动 Codexio macOS %s", __version__)
    try:
        controller = MacController(app, mock=args.mock)
        controller.start()
        if args.smoke_test:
            from codexio.macos_smoke import schedule_smoke_test
            schedule_smoke_test(controller, Path(args.smoke_test))
        return app.exec()
    finally:
        if controller is not None:
            controller.stop()
        instance.close()
