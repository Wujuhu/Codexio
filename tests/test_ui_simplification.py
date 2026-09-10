from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QMenu, QLabel

from codexio.analytics_config import load_analytics_config, save_analytics_config
from codexio.dashboard import Dashboard, PAGE_NAMES
from codexio.settings import AppSettings, load_settings, save_settings
from codexio.theme import apply_dark_menu, apply_theme, theme_colors
from codexio.window import QuotaWindow


def test_removed_network_settings_migrate_without_changing_appearance(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"window_x": 42, "visual_style": "rings", "quota_source": "remote",
                                "lan_share_enabled": True, "lan_access_token": "retired"}), encoding="utf-8")
    value = load_settings(path)
    save_settings(value, path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["window_x"] == 42 and saved["visual_style"] == "rings"
    assert "quota_source" not in saved
    assert not any(key.startswith("lan_") for key in saved)
    config_path = tmp_path / "analytics.json"
    config_path.write_text(json.dumps({"theme": "light", "lan_sources": [{"host": "retired"}], "share_tokens": True}), encoding="utf-8")
    config = load_analytics_config(config_path)
    save_analytics_config(config, config_path)
    assert config["theme"] == "light"
    assert "lan_sources" not in config and "share_tokens" not in config


def test_floating_window_and_menus_keep_dark_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    main = Dashboard(AppSettings(), {"theme": "light"}, {})
    widget = QuotaWindow(AppSettings(), lambda: None, lambda _: None, lambda: None)
    menu = QMenu(main)
    menu.addAction("退出")
    apply_dark_menu(menu)
    fixed = menu.styleSheet()
    for mode in ("dark", "light"):
        main.config_updated({"theme": mode})
        widget.apply_theme(mode)
        app.processEvents()
        assert not widget._theme_light
        assert widget._theme_text == theme_colors("dark")["text"]
        assert menu.styleSheet() == fixed
        assert menu.palette().color(QPalette.ColorRole.WindowText).name().upper() == theme_colors("dark")["text"]
    widget.close()
    main.hide()


def test_overview_hides_scrollbar_and_network_controls():
    app = QApplication.instance() or QApplication([])
    window = Dashboard(AppSettings(), {}, {})
    window.resize(1100, 760)
    window.open_page("overview")
    app.processEvents()
    area = window._stack.widget(0)
    assert area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert area.verticalScrollBar().maximum() > 0
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    assert area.verticalScrollBar().value() > 0
    window.open_page("settings")
    labels = [label.text() for label in window._stack.widget(PAGE_NAMES.index("settings")).findChildren(QLabel)]
    assert not any("局域网" in text for text in labels)
    assert "quota_source" not in window._setting_widgets
    window.hide()


def test_estimate_history_distinguishes_reset_time_and_sample_time():
    app = QApplication.instance() or QApplication([])
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("subscription")
    window.apply_data({"weekly_estimates": [{"status": "unattributed", "plan_type": "pro", "reset_at": 1790000000,
        "start": "2026-09-07T08:00:00+00:00", "end": "2026-09-07T09:00:00+00:00",
        "estimated_total_usd": None, "delta_percent": 3}]})
    assert window._estimate_value.text() == "待采样"
    assert window._estimate_period.text() == "正在积累服务端多日数据"
    window._show_estimates()
    app.processEvents()
    view = window._estimate_history_table
    assert view.horizontalHeaderItem(1).text() == "额度重置时间"
    assert view.horizontalHeaderItem(2).text() == "采样时间段"
    assert view.item(0, 3).text() == "—"
    assert view.item(0, 4).text() == "待确认账号"
    for dialog in list(window._dialogs):
        dialog.close()
    window.hide()
