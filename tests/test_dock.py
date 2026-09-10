from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QBoxLayout

from codexio.dock import (
    DOCK_BAR_MIN_LENGTH,
    DOCK_BOTTOM,
    DOCK_LEFT,
    DOCK_NONE,
    DOCK_RIGHT,
    DOCK_TOP,
    choose_dock_edge,
    clamp_dock_size,
    dock_content_scale,
    dock_min_size,
    dock_size,
    snap_geometry,
    snap_threshold,
)
from codexio.hit_test import HIT_DRAG, HIT_RESIZE
from codexio.settings import AppSettings
from codexio.window import QuotaWindow


def test_choose_dock_edge_prefers_top_on_corner() -> None:
    screen = QRect(0, 0, 1920, 1080)
    assert choose_dock_edge(QRect(40, 8, 320, 200), screen) == DOCK_TOP
    assert choose_dock_edge(QRect(8, 200, 320, 200), screen) == DOCK_LEFT
    assert choose_dock_edge(QRect(1920 - 320 - 8, 300, 320, 200), screen) == DOCK_RIGHT
    assert choose_dock_edge(QRect(400, 1080 - 200 - 8, 320, 200), screen) == DOCK_BOTTOM
    assert choose_dock_edge(QRect(400, 400, 320, 200), screen) == DOCK_NONE


def test_snap_geometry_pins_to_screen_edge() -> None:
    screen = QRect(0, 0, 1920, 1080)
    top = snap_geometry(DOCK_TOP, QRect(500, 12, 320, 200), screen)
    assert top.top() == 0
    assert top.size().width() == dock_size(DOCK_TOP)[0]
    assert top.size().height() == dock_size(DOCK_TOP)[1]
    left = snap_geometry(DOCK_LEFT, QRect(10, 300, 320, 200), screen)
    assert left.left() == 0
    right = snap_geometry(DOCK_RIGHT, QRect(1600, 300, 320, 200), screen)
    assert right.right() == screen.right()
    bottom = snap_geometry(DOCK_BOTTOM, QRect(500, 900, 320, 200), screen)
    assert bottom.bottom() == screen.bottom()
    assert bottom.size() == top.size()


def test_window_docks_horizontally_and_vertically(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.show()
    app.processEvents()
    assert window._is_docked() is False
    assert window.width() == 320
    assert window.height() == 200

    window.setGeometry(120, 4, 320, 200)
    window._snap_after_drag()
    app.processEvents()
    assert window._settings.dock_edge == DOCK_TOP
    assert window._pages.currentWidget() is window._dock_strip
    assert window._dock_strip._layout.direction() == QBoxLayout.Direction.LeftToRight
    assert window.height() == dock_size(DOCK_TOP)[1]
    assert window.hit_kind_at(window.rect().center()) == "drag"

    window.setGeometry(6, 240, window.width(), window.height())
    window._snap_after_drag()
    app.processEvents()
    assert window._settings.dock_edge == DOCK_LEFT
    assert window._dock_strip._layout.direction() == QBoxLayout.Direction.TopToBottom
    assert window.width() == dock_size(DOCK_LEFT)[0]

    screen = window._screen_rect()
    window.setGeometry(screen.center().x() - 160, screen.bottom() - 10, window.width(), window.height())
    window._snap_after_drag()
    app.processEvents()
    assert window._settings.dock_edge == DOCK_BOTTOM
    assert window._pages.currentWidget() is window._dock_strip
    assert window._dock_strip._layout.direction() == QBoxLayout.Direction.LeftToRight
    assert window.height() == dock_size(DOCK_BOTTOM)[1]
    assert window.y() + window.height() - 1 == screen.bottom()
    assert window.hit_kind_at(QPoint(window.width() // 2, window.height() - 2)) == HIT_DRAG
    assert window.hit_kind_at(QPoint(window.width() // 2, 2)) == HIT_RESIZE

    window.setGeometry(
        screen.center().x() - window.width() // 2,
        screen.center().y() - window.height() // 2,
        window.width(),
        window.height(),
    )
    window._snap_after_drag()
    app.processEvents()
    assert window._settings.dock_edge == DOCK_NONE
    assert window._pages.currentWidget() is window._float_page
    assert window.width() == 320
    assert window.height() == 200
    window.close()


def test_snap_preview_appears_while_near_edge(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.show()
    app.processEvents()
    screen = window._screen_rect()

    window.setGeometry(screen.left() + 120, screen.top() + 4, 320, 200)
    window._update_snap_preview()
    app.processEvents()
    assert window._preview_edge == DOCK_TOP
    assert window._snap_preview.isVisible()
    preview = window._snap_preview.geometry()
    assert preview.top() == screen.top()
    assert (preview.width(), preview.height()) == dock_size(DOCK_TOP)

    window.setGeometry(screen.left() + 200, screen.bottom() - 200 - 6, 320, 200)
    window._update_snap_preview()
    app.processEvents()
    assert window._preview_edge == DOCK_BOTTOM
    assert window._snap_preview.isVisible()
    preview = window._snap_preview.geometry()
    assert preview.bottom() == screen.bottom()
    assert (preview.width(), preview.height()) == dock_size(DOCK_BOTTOM)

    window.setGeometry(screen.center().x() - 160, screen.center().y() - 100, 320, 200)
    window._update_snap_preview()
    app.processEvents()
    assert window._preview_edge == DOCK_NONE
    assert window._snap_preview.isVisible() is False
    window.close()


def test_snap_threshold_is_stickier_when_docked() -> None:
    assert snap_threshold(False) == 24
    assert snap_threshold(True) == 40


def test_dock_strip_narrower_side_and_horizontal_bars(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.show()
    app.processEvents()

    assert dock_size(DOCK_LEFT)[0] < 80
    assert dock_size(DOCK_RIGHT)[0] == dock_size(DOCK_LEFT)[0]

    window._apply_dock(DOCK_TOP, persist=False)
    app.processEvents()
    assert dock_size(DOCK_TOP) == (642, 40)
    assert dock_size(DOCK_TOP)[1] == dock_min_size(DOCK_TOP)[1]
    assert dock_size(DOCK_LEFT)[0] == dock_min_size(DOCK_LEFT)[0]
    assert window._dock_strip._five._bar.isVisible()
    assert window._dock_strip._five._bar.minimumWidth() == DOCK_BAR_MIN_LENGTH
    assert window._dock_strip._five._bar.height() == 8
    assert window._dock_strip._five._box.direction() == QBoxLayout.Direction.LeftToRight
    assert window._dock_strip.refresh_button.text() == "刷新"

    window._apply_dock(DOCK_LEFT, persist=False)
    app.processEvents()
    assert window.width() == dock_size(DOCK_LEFT)[0]
    assert window._dock_strip._five._bar.isVisible() is False
    assert window._dock_strip.refresh_button.text() == "刷"
    tall = QRect(window.x(), window.y(), window.width(), 360)
    window.setGeometry(tall)
    app.processEvents()
    window._dock_strip.layout().activate()
    app.processEvents()
    assert window._dock_strip._five._bar.isVisible() is True
    assert window._dock_strip._five._bar.width() == 8
    window.resize(window.width(), dock_size(DOCK_LEFT)[1])
    app.processEvents()

    five = window._dock_strip._five
    week = window._dock_strip._week
    assert five._title.text() == "5\nhours"
    assert week._title.text() == "1\nweek"
    assert window.height() == dock_size(DOCK_LEFT)[1]
    assert dock_size(DOCK_LEFT)[1] >= 240
    five_title_bottom = five._title.mapTo(window, five._title.rect().bottomLeft()).y()
    five_percent_top = five._percent.mapTo(window, five._percent.rect().topLeft()).y()
    week_title_bottom = week._title.mapTo(window, week._title.rect().bottomLeft()).y()
    week_percent_top = week._percent.mapTo(window, week._percent.rect().topLeft()).y()
    five_percent_bottom = five._percent.mapTo(window, five._percent.rect().bottomLeft()).y()
    week_top = week._title.mapTo(window, QPoint(0, 0)).y()
    assert five_percent_top >= five_title_bottom
    assert week_percent_top >= week_title_bottom
    assert week_top - five_percent_bottom > five_percent_top - five_title_bottom
    window.close()


def test_week_only_hides_five_hour_everywhere(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from codexio.rate_limits import QuotaState, QuotaStatus, WindowView

    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.show()
    app.processEvents()
    week = WindowView(70, 30, 10080, None)
    window.apply_state(
        QuotaState(
            five_hour=WindowView.unavailable(),
            week=week,
            status=QuotaStatus.OK,
            message="ok",
        )
    )
    app.processEvents()
    assert window.shows_five_hour() is False
    assert window._styles["classic"]._five.isVisible() is False
    assert window._dock_strip is None

    window.apply_quota_scope("both", persist=False)
    app.processEvents()
    assert window.shows_five_hour() is True
    assert window._styles["classic"]._five.isVisible() is True

    window.apply_quota_scope("week", persist=False)
    window._apply_dock(DOCK_TOP, persist=False)
    app.processEvents()
    assert window.width() == dock_size(DOCK_TOP, week_only=True)[0]
    assert window._dock_strip._five.isVisible() is False
    window.close()


def test_snap_geometry_keeps_custom_size() -> None:
    screen = QRect(0, 0, 1920, 1080)
    top = snap_geometry(DOCK_TOP, QRect(500, 12, 320, 200), screen, size=(500, 64))
    assert top.top() == 0
    assert (top.width(), top.height()) == (500, 64)
    squeezed = clamp_dock_size(DOCK_TOP, 100, 10, week_only=False)
    assert squeezed == dock_min_size(DOCK_TOP)
    assert dock_min_size(DOCK_TOP)[0] < dock_size(DOCK_TOP)[0]
    assert dock_min_size(DOCK_LEFT)[0] == dock_size(DOCK_LEFT)[0]
    assert dock_min_size(DOCK_LEFT)[1] < dock_size(DOCK_LEFT)[1]


def test_docked_window_can_resize_width_and_height(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.show()
    app.processEvents()
    window._apply_dock(DOCK_TOP, persist=False)
    app.processEvents()
    default_w, default_h = dock_size(DOCK_TOP)
    min_w, min_h = dock_min_size(DOCK_TOP)
    assert window.width() == default_w
    assert window.hit_kind_at(QPoint(window.width() // 2, window.height() // 2)) == HIT_DRAG
    assert window.hit_kind_at(QPoint(window.width() // 2, window.height() - 3)) == HIT_RESIZE
    assert window.hit_kind_at(QPoint(window.width() // 2, 2)) == HIT_DRAG

    window._resize_edge = "b"
    window._resize_origin = QPoint(0, 0)
    window._resize_geom = window.geometry()
    window._apply_resize(QPoint(0, 16))
    assert window.height() == default_h + 16
    assert window.y() == window._screen_rect().top()

    window._resize_edge = "r"
    window._resize_origin = QPoint(0, 0)
    window._resize_geom = window.geometry()
    window._apply_resize(QPoint(-400, 0))
    assert window.width() == window.minimumWidth()
    assert window.width() >= min_w
    assert window.width() < default_w

    window._suspend_save = False
    window._capture_geometry()
    assert window._settings.dock_top_width == window.width()
    assert window.width() == window.minimumWidth()
    assert window.width() >= min_w
    assert window._settings.dock_top_height == default_h + 16
    assert window._settings.window_width == 320
    window.close()


def test_horizontal_dock_keeps_labels_and_grows_bars(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.show()
    app.processEvents()
    window._apply_dock(DOCK_TOP, persist=False)
    app.processEvents()

    min_w = window.minimumWidth()
    window.resize(min_w, window.height())
    app.processEvents()
    window._dock_strip.layout().activate()
    app.processEvents()
    five = window._dock_strip._five
    week = window._dock_strip._week
    assert five._title.width() >= five._title.sizeHint().width()
    assert week._title.width() >= week._title.sizeHint().width()
    assert five._title.text() == "5 hours"
    assert week._title.text() == "1 week"
    assert five._bar.width() >= DOCK_BAR_MIN_LENGTH
    short_bar = five._bar.width()

    window.resize(dock_size(DOCK_TOP)[0], window.height())
    app.processEvents()
    window._dock_strip.layout().activate()
    app.processEvents()
    assert five._bar.width() > short_bar
    assert week._bar.width() > short_bar
    window.close()


def test_dock_content_scale_follows_thickness() -> None:
    assert dock_content_scale(DOCK_TOP, 642, 40) == 1.0
    assert dock_content_scale(DOCK_TOP, 642, 80) == 2.0
    assert dock_content_scale(DOCK_LEFT, 52, 252) == 1.0
    assert dock_content_scale(DOCK_LEFT, 104, 252) == 2.0


def test_docked_text_and_bar_scale_with_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.show()
    app.processEvents()
    window._apply_dock(DOCK_TOP, persist=False)
    app.processEvents()
    five = window._dock_strip._five
    assert five._title.font().pixelSize() == 13
    assert five._percent.font().pixelSize() == 16
    assert five._bar.height() == 8

    window.resize(window.width(), 80)
    app.processEvents()
    window._dock_strip.layout().activate()
    app.processEvents()
    assert five._title.font().pixelSize() > 13
    assert five._percent.font().pixelSize() > 16
    assert five._bar.height() > 8
    assert five._title.width() >= five._title.sizeHint().width()

    window._apply_dock(DOCK_LEFT, persist=False)
    app.processEvents()
    tall = window.geometry()
    tall.setHeight(360)
    window.setGeometry(tall)
    app.processEvents()
    window._dock_strip.layout().activate()
    app.processEvents()
    assert window._dock_strip._five._bar.isVisible() is True
    assert window._dock_strip._five._bar.width() == 8
    title_px = window._dock_strip._five._title.font().pixelSize()
    window.resize(100, window.height())
    app.processEvents()
    window._dock_strip.layout().activate()
    app.processEvents()
    assert window._dock_strip._five._title.font().pixelSize() > title_px
    assert window._dock_strip._five._bar.width() > 8
    window.close()


def test_docked_quota_tooltip_shows_reset(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timedelta

    from codexio.rate_limits import QuotaState, QuotaStatus, WindowView

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    resets = datetime.now().astimezone() + timedelta(hours=2)
    window.apply_state(
        QuotaState(
            five_hour=WindowView(72, 28, 300, resets),
            week=WindowView(40, 60, 10080, resets),
            status=QuotaStatus.OK,
            message="ok",
        )
    )
    window._apply_dock(DOCK_TOP, persist=False)
    app.processEvents()
    five_tip = window._dock_strip._five.toolTip()
    week_tip = window._dock_strip._week.toolTip()
    assert "5 hours" in five_tip
    assert "72%" in five_tip
    assert "重置" in five_tip
    assert "1 week" in week_tip
    assert "40%" in week_tip
    assert "重置" in week_tip
    window.close()


def test_orb_style_uses_one_ball_and_prefers_five_hour(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timedelta

    from codexio.rate_limits import QuotaState, QuotaStatus, WindowView
    from codexio.visuals import ORB_MIN_SIZE, ORB_STYLE

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    window = QuotaWindow(AppSettings(), lambda: None, lambda _seconds: None, lambda: None)
    window.apply_visual_style(ORB_STYLE, persist=False, reset_size=True)
    app.processEvents()
    assert window._header.isVisible() is False
    assert window._status.isVisible() is False
    assert window.width() == window.height()
    resets = datetime.now().astimezone() + timedelta(hours=1)
    both = QuotaState(
        five_hour=WindowView(81, 19, 300, resets),
        week=WindowView(55, 45, 10080, resets),
        status=QuotaStatus.OK,
        message="ok",
    )
    window.apply_state(both)
    orb = window._styles[ORB_STYLE]
    assert "5 hours" in orb.hover_text()
    assert "81%" in orb.hover_text()
    assert "重置" in orb.hover_text()
    assert "1 week" not in orb.hover_text()

    week_only = QuotaState(
        five_hour=WindowView.unavailable(),
        week=WindowView(55, 45, 10080, resets),
        status=QuotaStatus.OK,
        message="ok",
    )
    window.apply_state(week_only)
    assert "1 week" in orb.hover_text()
    assert "55%" in orb.hover_text()

    assert window.hit_kind_at(QPoint(window.width() // 2, window.height() // 2)) == HIT_DRAG
    assert window.hit_kind_at(QPoint(window.width() - 6, window.height() // 2)) == HIT_RESIZE
    start = window.width()
    window._nudge_orb_size(24)
    assert window.width() == window.height() == start + 24
    window._resize_edge = "r"
    window._resize_origin = QPoint(0, 0)
    window._resize_geom = window.geometry()
    window._apply_resize(QPoint(36, 0))
    assert window.width() == window.height() == start + 60
    window._nudge_orb_size(-1000)
    app.processEvents()
    assert window.width() == window.height() == ORB_MIN_SIZE
    orb_origin = orb.mapTo(window, QPoint(0, 0))
    assert orb_origin.x() >= 0
    assert orb_origin.y() >= 0
    assert orb_origin.x() + orb.width() <= window.width()
    assert orb_origin.y() + orb.height() <= window.height()
    assert orb.width() >= window.width() - 2
    assert orb.height() >= window.height() - 2
    window.close()
