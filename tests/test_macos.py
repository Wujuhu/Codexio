from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from codexio import codex_discovery as discovery
from codexio import settings as settings_module
from codexio.dashboard import Dashboard
from codexio.menu_bar import MenuBarController, MenuBarPreview, cost_text, menu_bar_icon, popup_position
from codexio.rate_limits import QuotaState, QuotaStatus, WindowView
from codexio.settings import AppSettings, load_settings, save_settings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_mac_uses_application_support_and_allows_isolated_data_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("CODEXIO_DATA_DIR", raising=False)
    monkeypatch.setattr(settings_module, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert settings_module.data_dir() == tmp_path / "Library/Application Support/Codexio"
    isolated = tmp_path / "临时 验证"
    monkeypatch.setenv("CODEXIO_DATA_DIR", str(isolated))
    assert settings_module.data_dir() == isolated and isolated.is_dir()


def test_mac_settings_roundtrip_and_invalid_defaults(tmp_path):
    path = tmp_path / "settings.json"
    settings = AppSettings(menu_bar_preview_size="large", show_main_on_startup=False,
                           refresh_interval_seconds=30, codex_path="/Applications/ChatGPT.app")
    save_settings(settings, path)
    assert load_settings(path) == settings
    path.write_text('{"menu_bar_preview_size":"tiny", "show_main_on_startup":"false"}', encoding="utf-8")
    restored = load_settings(path)
    assert restored.menu_bar_preview_size == "comfortable" and not restored.show_main_on_startup


def test_app_hint_only_probes_resources_backend(tmp_path):
    bundle = tmp_path / "有空格 Codex.app"
    bundle.mkdir()
    assert list(discovery._hint_candidates(str(bundle))) == [bundle / "Contents/Resources/codex"]


def test_mac_discovery_works_without_shell_path(tmp_path, monkeypatch):
    binary = tmp_path / "ChatGPT.app/Contents/Resources/codex"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"native-cli")
    binary.chmod(0o755)
    monkeypatch.setattr(discovery, "IS_MACOS", True)
    monkeypatch.setattr(discovery.shutil, "which", lambda _: None)
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(discovery, "_macos_candidates", lambda: iter([binary]))
    monkeypatch.setattr(discovery, "_standard_app_roots", lambda: pytest.fail("Windows discovery on macOS"))
    monkeypatch.setattr(discovery, "_run_probe", lambda args, _: subprocess.CompletedProcess(args, 0,
                        b"codex-cli 0.1\n" if args[-1] == "--version" else b"app-server --listen", b""))
    discovery._PROBES.clear()
    assert discovery.find_codex() == binary
    assert discovery.find_codex(excluded=[binary]) is None


def test_standard_mac_candidates_include_both_apps_and_homebrew(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    paths = set(discovery._macos_candidates())
    assert Path("/Applications/Codex.app/Contents/Resources/codex") in paths
    assert Path("/Applications/ChatGPT.app/Contents/Resources/codex") in paths
    assert tmp_path / "Applications/Codex.app/Contents/Resources/codex" in paths
    assert Path("/opt/homebrew/bin/codex") in paths and Path("/usr/local/bin/codex") in paths


def test_mac_discovery_does_not_launch_desktop_or_nonexecutable_files(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "IS_MACOS", True)
    monkeypatch.setattr(discovery, "_run_probe", lambda *_: pytest.fail("Must not launch this file"))
    binary = tmp_path / "app.app/Contents/MacOS/codex"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"desktop")
    binary.chmod(0o755)
    assert not discovery.is_usable(binary)
    plain = tmp_path / "codex"
    plain.write_bytes(b"not executable")
    plain.chmod(0o644)
    assert not discovery.is_usable(plain)


@pytest.fixture
def preview(app):
    value = MenuBarPreview(AppSettings(), {"theme": "dark"}, on_open=lambda *_: None,
                           on_refresh=lambda: None, on_quit=lambda: None)
    yield value
    value.hide()
    value.deleteLater()
    app.processEvents()


def usage(now, **updates):
    result = dict(menu_bar_today={"tokens": 1_234_567, "usd": 12.34, "skipped": {"usd": 0}},
                  today_date=now.date().isoformat(), sources_complete=True, updated_at=now.isoformat(),
                  latest_request={"cost_usd": 1.56, "pricing_status": "priced", "request_status": "running",
                                  "call_count": 3, "timestamp": now.isoformat()})
    result.update(updates)
    return result


def test_preview_renders_daily_totals_and_whole_running_request(preview):
    now = datetime.now().astimezone()
    preview.apply_data(usage(now))
    preview.render(now)
    assert preview.today_cost.text() == "$12.34"
    assert preview.today_tokens.text() == "1.23M"
    assert preview.token_note.text() == "1,234,567 Token"
    assert preview.latest_cost.text() == "$1.56" and preview.latest_status.text() == "回复中"
    assert "3 次模型调用" in preview.latest_note.text() and "仍在更新" in preview.latest_note.text()


def test_compact_preview_displays_filtered_message_without_session_title_fallback(preview, app):
    now = datetime.now().astimezone()
    data = usage(now)
    prompt = ("<system>系统内容</system>\n# Files mentioned by the user:\n"
              "## screenshot.png: /var/folders/private.png\n## My request:\n")
    data["latest_request"].update(prompt_preview=prompt + "请缩小菜单栏预览 " * 30 + "[image 1]",
                                 session_title="不能当作用户输入")
    preview.apply_data(data)
    preview.show_at(QRect(100, 0, 20, 22))
    app.processEvents()
    assert preview.width() == 380
    assert preview.today_cost.font().pixelSize() == 23
    assert preview.latest_message.text.startswith("请缩小菜单栏预览")
    assert all(value not in preview.latest_message.text for value in ("系统内容", "Files mentioned", "/var/", "[image]"))
    assert 1 <= preview.latest_message.last_line_count <= 3
    assert preview.height() < 600
    preview.configure(replace(AppSettings(), menu_bar_preview_size="large"), {"theme": "dark"})
    assert preview.width() == 440
    data["latest_request"]["prompt_preview"] = "<system>系统内容</system>[image 1]"
    preview.apply_data(data)
    assert preview.latest_message.text == "未记录用户文字"
    assert preview.latest_message.toolTip() == ""


def test_date_rollover_never_displays_yesterdays_totals_as_today(preview):
    midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    preview.apply_data(usage(midnight - timedelta(seconds=1)))
    preview.render(midnight)
    assert preview.today_cost.text() == preview.today_tokens.text() == "—"
    assert "日期已切换" in preview.usage_status.text()
    assert preview.latest_cost.text() == "$1.56"
    preview.apply_data(usage(midnight, menu_bar_today={"tokens": 42, "usd": 0.01}))
    preview.render(midnight)
    assert preview.today_tokens.text() == "42"


def test_partial_unpriced_and_error_states_remain_explicit(preview):
    now = datetime.now().astimezone()
    data = usage(now, menu_bar_today={"tokens": 100, "usd": 0.05, "skipped": {"usd": 1}}, sources_complete=False)
    data["latest_request"].update(cost_usd=None, pricing_status="unpriced")
    preview.apply_data(data)
    preview.render(now)
    assert preview.today_cost.text() == "$0.05" and preview.cost_note.text() == "部分未定价"
    assert preview.latest_cost.text() == "未定价" and "尚未同步" in preview.usage_status.text()
    preview.set_usage_loading({"error": "日志读取失败"})
    preview.render(now)
    assert "用量读取失败" in preview.usage_status.text()
    assert preview.today_cost.text() == "$0.05"


def test_entirely_unpriced_day_is_distinct_from_an_empty_day(preview):
    now = datetime.now().astimezone()
    preview.apply_data(usage(now, menu_bar_today={"tokens": 100, "usd": None, "skipped": {"usd": 1}}))
    preview.render(now)
    assert preview.today_cost.text() == "未定价" and preview.today_tokens.text() == "100"
    preview.apply_data(usage(now, menu_bar_today={"tokens": None, "usd": None, "skipped": {"usd": 0}}))
    preview.render(now)
    assert preview.today_cost.text() == preview.today_tokens.text() == "—"


@pytest.mark.parametrize("value", [None, -1, True, float("nan"), float("inf"), "bad"])
def test_missing_or_invalid_cost_is_never_free(value):
    assert cost_text(value) == "—"
    assert cost_text(0) == "$0.00"
    assert cost_text(None, "unmetered") == "待计量"


def test_both_reset_times_stale_cache_and_reached_reset(preview):
    now = datetime.now().astimezone()
    five = WindowView(60, 40, 300, now + timedelta(hours=1))
    week = WindowView(20, 80, 10080, now + timedelta(days=2))
    preview.apply_quota(QuotaState(five, week, QuotaStatus.OK, "ok", now))
    preview.render(now)
    assert "60%" in preview.five_hour.value.text() and "重置" in preview.five_hour.reset.text()
    assert "20%" in preview.week.value.text() and "2 天" in preview.week.reset.text()
    preview.render(now + timedelta(hours=2))
    assert "上次缓存" in preview.quota_status.text()
    assert "等待额度更新" in preview.five_hour.reset.text()
    preview.configure(replace(AppSettings(), quota_scope="week"), {"theme": "light"})
    assert preview.five_hour.isHidden()


@pytest.mark.parametrize("screen,anchor", [
    (QRect(0, 25, 1470, 880), QRect(1450, 0, 20, 24)),
    (QRect(-1920, -1055, 1920, 1055), QRect(-1910, -1080, 20, 24)),
    (QRect(1470, 25, 1920, 1000), QRect(1480, 0, 20, 24)),
])
def test_preview_stays_within_each_screen(screen, anchor):
    size = QSize(520, 640)
    assert screen.contains(QRect(popup_position(anchor, size, screen), size))


def test_click_actions_escape_and_hidden_timer(app):
    events = []
    preview = MenuBarPreview(AppSettings(), {}, on_open=lambda page: events.append(page),
                             on_refresh=lambda: events.append("refresh"), on_quit=lambda: events.append("quit"))
    try:
        preview.show_at(QRect(100, 0, 20, 22))
        app.processEvents()
        assert preview._timer.isActive()
        preview.settings_button.click()
        assert events == ["settings"] and not preview.isVisible() and not preview._timer.isActive()
        preview.show_at(QRect(100, 0, 20, 22))
        QTest.keyClick(preview, Qt.Key.Key_Escape)
        assert not preview.isVisible() and not preview._timer.isActive()
        preview.refresh_button.click()
        preview.quit_button.click()
        assert events[-2:] == ["refresh", "quit"]
        assert menu_bar_icon().isMask()
    finally:
        preview.deleteLater()


def test_mac_dashboard_excludes_widget_and_windows_updater_and_saves_settings(app):
    saved = []
    window = Dashboard(AppSettings(), {}, {"settings": saved.append}, desktop_platform="macos")
    try:
        window.open_page("settings")
        assert window._widget_toggle.isHidden()
        assert [window._settings_sections.item(i).text() for i in range(4)] == ["外观", "菜单栏", "数据来源", "应用"]
        assert not hasattr(window, "_auto_update") and not hasattr(window, "_widget_preview")
        window._setting_widgets["refresh_interval_seconds"].setCurrentIndex(0)
        window._setting_widgets["menu_bar_preview_size"].setCurrentIndex(1)
        window._setting_widgets["show_main_on_startup"].setChecked(False)
        window._save_settings()
        assert saved[-1].menu_bar_preview_size == "large" and not saved[-1].show_main_on_startup
        assert saved[-1].refresh_interval_seconds == 30
    finally:
        window.close()


def test_single_instance_activates_owner_and_releases_lock(app, tmp_path):
    from codexio.macos_app import SingleInstance
    events = []
    owner = SingleInstance(tmp_path, lambda: events.append("open"))
    second = SingleInstance(tmp_path, lambda: pytest.fail("second instance owns the socket"))
    try:
        assert owner.acquire()
        assert not second.acquire()
        app.processEvents()
        assert events == ["open"]
    finally:
        second.close()
        owner.close()
    third = SingleInstance(tmp_path, lambda: None)
    try:
        assert third.acquire()
    finally:
        third.close()


def test_changed_cli_path_reconnects_in_worker_thread(monkeypatch):
    from codexio.worker import QuotaWorker
    worker = QuotaWorker(AppSettings(codex_path="/old"))
    events = []
    monkeypatch.setattr(worker, "_close_client", lambda: events.append("close"))
    monkeypatch.setattr(worker, "_fetch_local", lambda: events.append("fetch"))
    worker.update_settings(AppSettings(codex_path="/new"))
    worker._run_live_cycle()
    worker._run_live_cycle()
    assert events == ["close", "fetch", "fetch"]
