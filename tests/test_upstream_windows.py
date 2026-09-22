"""Platform orchestration tests; actual WM_CLOSE/MSIX activation needs Windows."""
from types import SimpleNamespace

import pytest

from codexio import upstream_client as client


@pytest.mark.parametrize('aumid', ['', 'OpenAI.Codex_abcdef!App'])
def test_windows_restarts_only_original_desktop_and_uses_package_identity(tmp_path, monkeypatch, aumid):
    exe = tmp_path / 'Codex Desktop/Codex.exe'
    exe.parent.mkdir()
    exe.touch()
    old = {'pid': 10, 'created': 1.0, 'exe': str(exe), 'aumid': aumid}
    running = [old]
    events = []
    monkeypatch.setattr(client, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(client, 'running_clients', lambda: list(running))
    monkeypatch.setattr(client, 'alive', lambda value: value in running)
    monkeypatch.setattr(client.psutil, 'Process', lambda _: SimpleNamespace(children=lambda **_: []))
    def close(value):
        events.append(('close', value['pid']))
        running.remove(value)
    monkeypatch.setattr(client, '_close_client', close)
    def launch(value, **kwargs):
        events.append(('launch', value))
        running.append(dict(old, pid=20))
    monkeypatch.setattr(client.os, 'startfile', launch, raising=False)
    monkeypatch.setattr(client.subprocess, 'Popen', launch)
    assert client.restart_running()
    assert events[0] == ('close', 10)
    assert events[1] == ('launch', 'shell:AppsFolder\\' + aumid if aumid else [str(exe)])
    assert len(events) == 2


def test_no_running_desktop_never_launches_a_client(monkeypatch):
    monkeypatch.setattr(client, 'running_clients', lambda: [])
    monkeypatch.setattr(client.subprocess, 'Popen', lambda *_: pytest.fail('Must not open an absent client'))
    assert client.restart_running() is False
