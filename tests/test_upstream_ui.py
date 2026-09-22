from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from codexio.analytics_config import default_config
from codexio.dashboard import Dashboard
from codexio.desktop_widgets import LedgerTable, UPSTREAM_ROLE
from codexio.settings import AppSettings
from codexio.upstream_manager import UpstreamManager


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


class FakeService:
    def __init__(self):
        self.active = False
        self.state = {}
        self.calls = []

    def recover(self, **kwargs):
        self.calls.append(('recover', kwargs))
        return self.active

    def enable(self):
        self.active = True
        self.calls.append('enable-and-restart')
        return '已开启'

    def disable(self):
        self.active = False
        self.calls.append('restore-and-restart')
        return '已关闭'

    def prepare_update(self):
        self.calls.append('handoff')

    def abandon(self):
        self.calls.append('abandon-without-restart')

    def healthy(self):
        return self.active

    @property
    def needs_restore(self):
        return self.active


@pytest.fixture
def manager(app, tmp_path, monkeypatch):
    config = default_config()
    service = FakeService()
    value = UpstreamManager(app, tmp_path, lambda: config, lambda updated: config.update(updated), lambda: None, service=service)
    value._confirm = lambda **_: True
    monkeypatch.setattr(QMessageBox, 'warning', lambda *_: None)
    yield value, config, service
    value.stop()
    app.removeEventFilter(value)
    value.deleteLater()
    app.processEvents()


def settle(app, manager):
    deadline = time.monotonic() + 3
    while manager.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not manager.busy


def test_manual_enable_quit_preserves_intent_and_disable_clears_it(app, manager):
    value, config, service = manager
    value.toggle(True)
    settle(app, value)
    assert value.active and config['upstream_detection_enabled']
    exits = []
    value.request_quit(lambda: exits.append(True))
    settle(app, value)
    assert exits == [True] and config['upstream_detection_enabled'] and not value.active
    assert service.calls == ['enable-and-restart', 'restore-and-restart']
    value.toggle(True)
    settle(app, value)
    value.toggle(False)
    settle(app, value)
    assert not config['upstream_detection_enabled'] and not value.active


def test_cancel_startup_keeps_preference_and_quit_does_not_restart(app, manager):
    value, config, service = manager
    config['upstream_detection_enabled'] = True
    prompts = []
    value._confirm = lambda **options: prompts.append(options) or False
    value.start()
    settle(app, value)
    assert prompts == [{}] and not value.active and config['upstream_detection_enabled']
    value.request_quit(lambda: None)
    assert 'restore-and-restart' not in service.calls
    value.toggle(False)
    assert not config['upstream_detection_enabled'] and 'restore-and-restart' not in service.calls


def test_quit_cancel_and_suppression_are_independent_of_startup(app, manager):
    value, config, service = manager
    value.toggle(True)
    settle(app, value)
    value._confirm = lambda **_: False
    exits = []
    value.request_quit(lambda: exits.append(True))
    assert not exits and value.active
    config['upstream_exit_prompt'] = False
    value.request_quit(lambda: exits.append(True))
    settle(app, value)
    assert exits == [True] and config['upstream_detection_enabled']
    prompts = []
    value._confirm = lambda **_: prompts.append(True) or False
    value.start()
    settle(app, value)
    assert prompts == [True]


def test_self_update_and_system_exit_never_restart_chatgpt(app, manager, monkeypatch):
    value, config, service = manager
    config['upstream_detection_enabled'] = True
    service.active = True
    monkeypatch.setenv('CODEXIO_UPDATE_JOB', '/isolated/update/job')
    value._confirm = lambda **_: pytest.fail('No repeat prompt during self-update handoff')
    value.start()
    settle(app, value)
    assert value.active and service.calls == [('recover', {'update': True})]
    value.quit_for_update(lambda: None)
    assert service.calls[-1] == 'handoff'
    value._system_quit(None)
    assert service.calls[-1] == 'abandon-without-restart'
    assert 'restore-and-restart' not in service.calls


def test_native_quit_event_is_intercepted_but_window_close_is_not(app, manager):
    value, _, service = manager
    exits = []
    value.on_quit = lambda: exits.append(True)
    assert not value.eventFilter(app, QEvent(QEvent.Type.Close))
    assert value.eventFilter(app, QEvent(QEvent.Type.Quit))
    assert exits == [True] and not service.calls


@pytest.mark.parametrize('platform', ['macos', 'windows'])
def test_settings_control_on_both_platforms_and_no_restart_button(app, platform):
    changes = []
    config = default_config()
    config['upstream_detection_enabled'] = True
    window = Dashboard(AppSettings(), config, {'upstream_toggle': changes.append}, desktop_platform=platform)
    try:
        window.open_page('settings')
        window._settings_sections.setCurrentRow(3)
        window.set_upstream_status('本次未启用；下次仍询问', False, False)
        assert window._upstream_toggle.isChecked()
        window._upstream_toggle.click()
        assert changes == [False]
        window.set_upstream_status('正在开启', False, True)
        assert not window._upstream_toggle.isEnabled()
        from PySide6.QtWidgets import QPushButton
        assert not any('重启' in button.text() for button in window.findChildren(QPushButton))
    finally:
        window.close()


def test_upstream_badge_keeps_original_text_and_survives_disable(app):
    rows = [dict(id='r1', model='requested', models=['requested'], call_count=3, cost_usd=1.25,
                 upstream_models=['actual-a', 'actual-b'], upstream_model_counts={'actual-a': 1, 'actual-b': 1},
                 upstream_detected_calls=2, upstream_total_calls=3), dict(id='old', model='requested')]
    table = LedgerTable()
    for theme in ('light', 'dark'):
        table.set_records(rows, lambda row: str(row.get('cost_usd')), theme)
        assert table.item(0, 1).text() == 'requested'
        assert table.item(0, 1).data(UPSTREAM_ROLE) == '多上游（2）'
        assert '2 / 3' in table.item(0, 1).toolTip()
        assert table.item(1, 1).data(UPSTREAM_ROLE) is None
        table.resize(1000, 200)
        table.show()
        assert not table.grab().isNull()
    table.close()
