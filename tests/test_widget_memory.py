from __future__ import annotations

import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractAnimation, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from aiquota.rate_limits import QuotaState, QuotaStatus, WindowView
from aiquota.settings import AppSettings, VISUAL_STYLES
from aiquota.visuals import PercentAnimator
from aiquota.window import QuotaWindow


def drain(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture
def make_widget(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    app = QApplication.instance() or QApplication([])
    windows = []
    def create(**kwargs):
        window = QuotaWindow(AppSettings(**kwargs), lambda: None, lambda _: None, lambda: None)
        windows.append(window)
        drain(app)
        return window
    yield create, app
    for window in windows:
        window.close()
        window.deleteLater()
    drain(app)


def state(percent=72, *, week_only=False):
    reset = datetime.now().astimezone() + timedelta(hours=2)
    return QuotaState(
        five_hour=WindowView.unavailable() if week_only else WindowView(percent, 100-percent, 300, reset),
        week=WindowView(percent-10, 110-percent, 10080, reset),
        status=QuotaStatus.OK, message="ok", last_success_at=datetime.now().astimezone(),
    )


@pytest.mark.parametrize("style", VISUAL_STYLES)
def test_startup_constructs_only_selected_style_and_no_unused_dock(make_widget, style):
    create, app = make_widget
    window = create(visual_style=style)
    assert list(window._styles) == [style]
    assert window._stack.count() == 1
    assert window._dock_strip is None
    assert window._snap_preview is None
    assert window._tick.isActive()


def test_style_switches_delete_previous_qt_widgets_instead_of_caching_them(make_widget):
    create, app = make_widget
    window = create()
    latest = state()
    window.apply_state(latest)
    for style in (*VISUAL_STYLES[1:], VISUAL_STYLES[0]):
        old = window._current_style()
        window.apply_visual_style(style, persist=False, reset_size=True)
        drain(app)
        assert not isValid(old)
        assert list(window._styles) == [style]
        assert window._stack.count() == 1
        assert window._current_style()._state is latest
        assert window._current_style().parent() is window._stack


def test_dock_and_floating_widgets_are_created_only_for_active_variant(make_widget):
    create, app = make_widget
    window = create(visual_style="orb")
    latest = state()
    window.apply_state(latest)
    old_style = window._current_style()
    window._apply_dock("top", persist=False)
    drain(app)
    assert not isValid(old_style)
    assert window._styles == {} and window._stack.count() == 0
    dock = window._dock_strip
    assert window._pages.currentWidget() is dock
    for edge in ("left", "right", "bottom", "top"):
        window._apply_dock(edge, persist=False)
        drain(app)
        assert window._dock_strip is dock
        assert window._styles == {}
        assert "重置" in dock._week.toolTip()
    window.apply_visual_style("rings", persist=False)
    assert window._styles == {}
    window._apply_dock("none", persist=False)
    drain(app)
    assert not isValid(dock)
    assert window._dock_strip is None
    assert list(window._styles) == ["rings"]
    assert window._current_style()._state is latest


@pytest.mark.parametrize("style,edge", [("classic", "none"), ("orb", "none"), ("classic", "top")])
def test_hidden_updates_coalesce_without_rendering_and_restore_on_show(make_widget, monkeypatch, style, edge):
    create, app = make_widget
    window = create(visual_style=style, dock_edge=edge)
    window.apply_state(state(90))
    window.apply_usage_summary(dict(tokens=1000, usd=1, unpriced_tokens=0))
    active = window._dock_strip if edge != "none" else window._current_style()
    window.hide()
    drain(app)
    assert not window._tick.isActive()
    assert all(a._anim.state() == QAbstractAnimation.State.Stopped for a in window.findChildren(PercentAnimator))
    if style == "orb":
        assert not active._timer.isActive()
    calls = []
    actual_apply = active.apply
    def apply(value):
        calls.append(value)
        return actual_apply(value)
    monkeypatch.setattr(active, "apply", apply)
    old_text = window._status.text()
    latest = None
    for percent in (80, 70, 60):
        latest = state(percent)
        window.apply_state(latest)
        window.apply_usage_summary(dict(tokens=percent*100, usd=percent, unpriced_tokens=0))
    drain(app)
    assert not calls
    assert window._status.text() == old_text
    assert not window._tick.isActive()
    window.show()
    drain(app)
    assert calls == [latest]
    assert window._status.text() == "今日 6.0k Token $60.00"
    assert window._tick.isActive()
    if style == "orb":
        assert active._timer.isActive()
        assert "60%" in window.toolTip()
    elif edge != "none":
        assert "60%" in active._five.toolTip()
    else:
        assert active._state is latest


def test_hidden_week_only_change_restores_dock_scope_and_geometry(make_widget):
    create, app = make_widget
    window = create(dock_edge="top", dock_top_width=700, dock_top_height=60)
    window.apply_state(state())
    drain(app)
    before = window.geometry()
    window.hide()
    latest = state(68, week_only=True)
    window.apply_state(latest)
    assert window.geometry() == before
    window.show()
    drain(app)
    assert not window.shows_five_hour()
    assert not window._dock_strip._five.isVisible()
    assert window.width() == 700 and window.height() == 60
    assert window.geometry().top() == window._screen_rect().top()


def test_tray_start_keeps_status_timer_stopped_until_first_show(make_widget):
    create, app = make_widget
    window = create(display_mode="tray")
    assert not window.isVisible()
    assert not window._tick.isActive()
    latest = state(57)
    window.apply_state(latest)
    window.apply_usage_summary(dict(tokens=500, usd=None, unpriced_tokens=500))
    assert window._current_style()._state is not latest
    window.toggle_visibility()
    drain(app)
    assert window._current_style()._state is latest
    assert window._tick.isActive()
    assert window._status.text() == "今日 500 Token 费用未定价"
