from __future__ import annotations

from codexio.settings import (
    DEFAULT_BACKGROUND_OPACITY,
    DEFAULT_BORDER_COLOR,
    DEFAULT_QUOTA_SCOPE,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_VISUAL_STYLE,
    AppSettings,
    load_settings,
    save_settings,
)
from codexio.visuals import (
    QUOTA_FIVE_LABEL,
    QUOTA_WEEK_LABEL,
    STYLE_LABELS,
    STYLE_WINDOW_SIZES,
    format_percent_label,
    style_preferred_window_size,
)


def test_unknown_style_falls_back() -> None:
    settings = AppSettings(visual_style="neon-glass").normalized()
    assert settings.visual_style == DEFAULT_VISUAL_STYLE


def test_window_size_is_clamped() -> None:
    settings = AppSettings(window_width=20, window_height=4000).normalized()
    assert settings.window_width == 200
    assert settings.window_height == 800


def test_settings_roundtrip_keeps_style_and_size(tmp_path) -> None:
    path = tmp_path / "settings.json"
    original = AppSettings(
        visual_style="rings",
        window_width=420,
        window_height=240,
        display_mode="bottom",
        background_transparent=True,
        background_opacity=40,
        show_border=False,
        border_color="#ff8800",
        dock_edge="top",
        dock_top_width=500,
        dock_top_height=56,
        dock_side_width=70,
        dock_side_height=280,
        quota_scope="week",
    ).normalized()
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded.visual_style == "rings"
    assert loaded.window_width == 420
    assert loaded.window_height == 240
    assert loaded.display_mode == "bottom"
    assert loaded.background_transparent is True
    assert loaded.background_opacity == 40
    assert loaded.show_border is False
    assert loaded.border_color == "#FF8800"
    assert loaded.dock_edge == "top"
    assert loaded.dock_top_width == 500
    assert loaded.dock_top_height == 56
    assert loaded.dock_side_width == 70
    assert loaded.dock_side_height == 280
    assert loaded.quota_scope == "week"


def test_old_settings_file_gets_style_default(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"refresh_interval_seconds": 30, "display_mode": "top"}', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.visual_style == DEFAULT_VISUAL_STYLE
    assert loaded.window_width is None
    assert loaded.refresh_interval_seconds == 30
    assert loaded.background_transparent is True
    assert loaded.show_border is True
    assert loaded.border_color == DEFAULT_BORDER_COLOR
    assert loaded.background_opacity == DEFAULT_BACKGROUND_OPACITY
    assert loaded.dock_edge == "none"
    assert loaded.quota_scope == DEFAULT_QUOTA_SCOPE


def test_missing_refresh_uses_one_minute(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"display_mode": "top"}', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.refresh_interval_seconds == 60


def test_legacy_fifteen_second_refresh_falls_back_to_default(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"refresh_interval_seconds": 15}', encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.refresh_interval_seconds == DEFAULT_REFRESH_INTERVAL == 60


def test_new_settings_use_requested_defaults() -> None:
    settings = AppSettings().normalized()
    assert settings.refresh_interval_seconds == DEFAULT_REFRESH_INTERVAL == 60
    assert settings.display_mode == "top"
    assert settings.visual_style == "classic"
    assert settings.background_transparent is True
    assert settings.background_opacity == DEFAULT_BACKGROUND_OPACITY == 70
    assert settings.show_border is True
    assert settings.background_alpha() == 76
    assert settings.window_width is None
    assert settings.window_height is None
    assert settings.dock_edge == "none"
    assert settings.quota_scope == "auto"


def test_transparent_opacity_zero_stays_zero() -> None:
    settings = AppSettings(background_transparent=True, background_opacity=0).normalized()
    assert settings.background_opacity == 0
    assert settings.background_alpha() == 255


def test_full_transparency_is_clear() -> None:
    settings = AppSettings(background_transparent=True, background_opacity=100).normalized()
    assert settings.background_alpha() == 0


def test_invalid_border_color_falls_back() -> None:
    settings = AppSettings(border_color="blue").normalized()
    assert settings.border_color == DEFAULT_BORDER_COLOR








def test_every_style_has_label_and_default_size() -> None:
    from codexio.settings import VISUAL_STYLES

    assert set(STYLE_LABELS) == set(VISUAL_STYLES)
    for name in STYLE_LABELS:
        width, height = style_preferred_window_size(name)
        assert (width, height) == STYLE_WINDOW_SIZES[name]
        if name == "orb":
            assert width == height
            assert 120 <= width <= 200
            continue
        assert width >= 200
        assert height >= 96
        assert width <= 360
        assert height <= 228


def test_orb_quota_picks_five_hour_then_week() -> None:
    from datetime import datetime

    from codexio.rate_limits import QuotaState, QuotaStatus, WindowView
    from codexio.visuals import QUOTA_FIVE_LABEL, QUOTA_WEEK_LABEL, quota_hover_text, resolve_orb_quota

    week = WindowView(40, 60, 10080, datetime.now().astimezone())
    five = WindowView(80, 20, 300, datetime.now().astimezone())
    both = QuotaState(five, week, QuotaStatus.OK, "ok")
    only_week = QuotaState(WindowView.unavailable(), week, QuotaStatus.OK, "ok")
    title, view = resolve_orb_quota(both)
    assert title == QUOTA_FIVE_LABEL
    assert view.remaining_percent == 80
    title, view = resolve_orb_quota(only_week)
    assert title == QUOTA_WEEK_LABEL
    assert view.remaining_percent == 40
    title, view = resolve_orb_quota(both, week_only=True)
    assert title == QUOTA_WEEK_LABEL
    tip = quota_hover_text(QUOTA_FIVE_LABEL, five)
    assert "5 hours" in tip
    assert "80%" in tip
    assert "重置" in tip


def test_quota_labels_are_english() -> None:
    assert QUOTA_FIVE_LABEL == "5 hours"
    assert QUOTA_WEEK_LABEL == "1 week"
    assert format_percent_label(None) == "N/A"
    assert format_percent_label(93.6) == "94%"


def test_bundled_sans_font_registers() -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QApplication, QLabel

    from codexio.visuals import (
        bundled_font_path,
        display_font,
        display_font_css,
        display_font_family,
        register_bundled_fonts,
    )
    from codexio.window import _STYLESHEET

    app = QApplication.instance() or QApplication([])
    assert bundled_font_path().is_file()
    family = register_bundled_fonts()
    assert family == "Anthropic Sans Web"
    assert display_font_family() == "Anthropic Sans Web"
    font = display_font(16, bold=True)
    info = QFontInfo(font)
    assert info.family() == "Anthropic Sans Web"
    assert "font-feature-settings" not in _STYLESHEET
    label = QLabel("94%")
    label.setStyleSheet(_STYLESHEET % display_font_css())
    label.setFont(display_font(16))
    assert QFontInfo(label.font()).family() == "Anthropic Sans Web"
    del app


def test_codexio_reuses_existing_user_data_after_rename(tmp_path, monkeypatch):
    from codexio.settings import data_dir
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "AIQuotaWidget"
    legacy.mkdir()
    (legacy / "settings.json").write_text('{"refresh_interval_seconds": 300}', encoding="utf-8")
    assert data_dir() == legacy
    assert load_settings().refresh_interval_seconds == 300
    assert not (tmp_path / "Codexio").exists()


def test_codexio_new_install_and_existing_new_profile(tmp_path, monkeypatch):
    from codexio.settings import data_dir
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "Codexio"
    legacy = tmp_path / "AIQuota"
    legacy.mkdir()
    (legacy / "settings.json").write_text('{"refresh_interval_seconds": 300}', encoding="utf-8")
    assert data_dir() == tmp_path / "Codexio"
