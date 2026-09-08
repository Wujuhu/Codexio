from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from aiquota import codex_discovery as discovery


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
    monkeypatch.setattr(discovery.shutil, "which", lambda _name: None)
    monkeypatch.setattr(discovery, "_registry_app_roots", lambda: iter(()))
    monkeypatch.setattr(discovery, "_windows_app_locations", lambda: ([], []))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "Home")
    discovery._PROBES.clear()
    return tmp_path


def make_exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZtest")
    return path


def allow(monkeypatch, *paths):
    monkeypatch.setattr(discovery, "is_usable", lambda path: path in paths and path.is_file())


def test_app_only_finds_version_directory_and_rediscovers_after_update(isolated, monkeypatch):
    root = isolated / "Local" / "OpenAI" / "Codex" / "bin"
    old = make_exe(root / "old-hash" / "codex.exe")
    allow(monkeypatch, old)
    assert discovery.find_codex() == old
    old.unlink()
    new = make_exe(root / "a-different-hash" / "codex.exe")
    allow(monkeypatch, new)
    assert discovery.find_codex() == new


def test_stale_explicit_custom_app_path_recovers_from_version_change(isolated, monkeypatch):
    stale = isolated / "Custom App" / "bin" / "retired-version" / "codex.exe"
    current = make_exe(stale.parent.parent / "new-version" / "codex.exe")
    allow(monkeypatch, current)
    assert discovery.find_codex(str(stale)) == current


def test_newer_valid_backend_preferred_and_invalid_backend_skipped(isolated, monkeypatch):
    root = isolated / "Local" / "OpenAI" / "Codex" / "bin"
    old = make_exe(root / "codex.exe")
    current = make_exe(root / "current" / "codex.exe")
    broken = make_exe(root / "partial-update" / "codex.exe")
    for stamp, path in enumerate((old, current, broken), 1):
        os.utime(path, (stamp, stamp))
    allow(monkeypatch, old, current)
    assert discovery.find_codex() == current
    assert discovery.find_codex(excluded={current}) == old


def test_msix_install_location_is_queried_again_after_app_update(isolated, monkeypatch):
    first = make_exe(isolated / "WindowsApps" / "OpenAI.Codex_1" / "app" / "resources" / "codex.exe")
    second = make_exe(isolated / "WindowsApps" / "OpenAI.Codex_2" / "app" / "resources" / "codex.exe")
    locations = [first.parents[2]]
    monkeypatch.setattr(discovery, "_windows_app_locations", lambda: (locations, []))
    allow(monkeypatch, first, second)
    assert discovery.find_codex() == first
    locations[:] = [second.parents[2]]
    assert discovery.find_codex() == second


def test_registry_finds_custom_installer_location(isolated, monkeypatch):
    backend = make_exe(isolated / "Custom Location" / "resources" / "native" / "codex.exe")
    monkeypatch.setattr(discovery, "_registry_app_roots", lambda: iter([backend.parents[2]]))
    allow(monkeypatch, backend)
    assert discovery.find_codex() == backend


def test_electron_ui_is_never_launched_as_a_probe(isolated, monkeypatch):
    gui = make_exe(isolated / "Desktop" / "Codex.exe")
    make_exe(gui.parent / "resources" / "app.asar")
    monkeypatch.setattr(discovery, "_run_probe", lambda *_: pytest.fail("Desktop UI must not launch"))
    assert not discovery.is_usable(gui)


def test_probe_requires_a_cli_and_compatible_app_server(isolated, monkeypatch):
    exe = make_exe(isolated / "bin" / "codex.exe")
    calls = []
    def probe(args, timeout):
        calls.append(args[1:])
        output = b"codex-cli 0.153.4\n" if args[-1] == "--version" else b"Usage: codex app-server [OPTIONS]\n--listen <URL> stdio://"
        return subprocess.CompletedProcess(args, 0, output, b"")
    monkeypatch.setattr(discovery, "_run_probe", probe)
    assert discovery.is_usable(exe)
    assert calls == [["--version"], ["app-server", "--help"]]
    assert discovery.is_usable(exe) and len(calls) == 2
    exe.write_bytes(b"MZreplacement with another size")
    assert discovery.is_usable(exe) and len(calls) == 4


@pytest.mark.parametrize("output", [b"Codex Desktop", b"codex-cli 0.1.0"])
def test_gui_version_or_unsupported_server_is_rejected(isolated, monkeypatch, output):
    exe = make_exe(isolated / "codex.exe")
    monkeypatch.setattr(discovery, "_run_probe", lambda args, _timeout: subprocess.CompletedProcess(args, 0, output, b""))
    assert not discovery.is_usable(exe)


def test_failed_probe_does_not_stop_discovery(isolated, monkeypatch):
    exe = make_exe(isolated / "codex.exe")
    def timeout(args, _timeout):
        raise subprocess.TimeoutExpired(args, 4)
    monkeypatch.setattr(discovery, "_run_probe", timeout)
    assert not discovery.is_usable(exe)


def test_npm_shim_prefers_native_binary_without_node(isolated, monkeypatch):
    shim = make_exe(isolated / "npm" / "codex.cmd")
    native = make_exe(shim.parent / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64" / "bin" / "codex.exe")
    allow(monkeypatch, shim, native)
    assert discovery.find_codex(str(shim)) == native


@pytest.mark.parametrize("frozen", [False, True], ids=["source", "packaged"])
def test_saved_forwarding_shim_prefers_app_backend(isolated, monkeypatch, frozen):
    shim = make_exe(isolated / "Home" / ".local" / "bin" / "codex.cmd")
    backend = make_exe(isolated / "Local" / "OpenAI" / "Codex" / "bin" / "current" / "codex.exe")
    monkeypatch.setattr(discovery.sys, "frozen", frozen, raising=False)
    allow(monkeypatch, shim, backend)
    assert discovery.find_codex(str(shim)) == backend
    # A native startup failure must leave the known working wrapper available.
    assert discovery.find_codex(str(shim), excluded={backend}) == shim


def test_forwarding_shim_prefers_its_own_native_package_version(isolated, monkeypatch):
    shim = make_exe(isolated / "Home" / ".local" / "bin" / "codex.cmd")
    npm_shim = make_exe(isolated / "Roaming" / "npm" / "codex.cmd")
    shim.write_text('@echo off\ncall "%APPDATA%\\npm\\codex.cmd" %*\n', encoding="utf-8")
    native = make_exe(npm_shim.parent / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64" / "bin" / "codex.exe")
    app = make_exe(isolated / "Local" / "OpenAI" / "Codex" / "bin" / "current" / "codex.exe")
    allow(monkeypatch, shim, npm_shim, native, app)
    assert discovery.find_codex(str(shim)) == native
    assert discovery.find_codex(str(shim), excluded={native}) == app
    assert discovery.find_codex(str(shim), excluded={native, app}) == shim


def test_path_wrapper_does_not_hide_app_only_msix_install(isolated, monkeypatch):
    shim = make_exe(isolated / "old-cli" / "codex.cmd")
    backend = make_exe(isolated / "Custom Drive" / "WindowsApps" / "OpenAI.Codex_3" / "app" / "resources" / "codex.exe")
    monkeypatch.setattr(discovery.shutil, "which", lambda name: str(shim) if name == "codex.cmd" else None)
    monkeypatch.setattr(discovery, "_windows_app_locations", lambda: ([backend.parents[2]], []))
    allow(monkeypatch, shim, backend)
    assert discovery.find_codex() == backend


def test_app_only_update_ignores_stale_cli_and_msix_path(isolated, monkeypatch):
    stale = isolated / "Custom Drive" / "WindowsApps" / "OpenAI.Codex_1" / "app" / "resources" / "codex.exe"
    backend = make_exe(isolated / "Custom Drive" / "WindowsApps" / "OpenAI.Codex_2" / "app" / "resources" / "codex.exe")
    monkeypatch.setenv("CODEX_CLI_PATH", str(isolated / "removed-cli" / "codex.cmd"))
    monkeypatch.setattr(discovery, "_windows_app_locations", lambda: ([backend.parents[2]], []))
    allow(monkeypatch, backend)
    assert discovery.find_codex(str(stale)) == backend


def test_shim_still_works_when_no_native_backend_is_available(isolated, monkeypatch):
    shim = make_exe(isolated / "npm" / "codex.cmd")
    allow(monkeypatch, shim)
    assert discovery.find_codex(str(shim)) == shim


def test_native_backend_is_returned_without_probing_wrapper(isolated, monkeypatch):
    shim = make_exe(isolated / "Home" / ".local" / "bin" / "codex.cmd")
    backend = make_exe(isolated / "Local" / "OpenAI" / "Codex" / "bin" / "current" / "codex.exe")
    def usable(path):
        assert path != shim, "A native connection should not launch a cmd/Node probe"
        return path == backend
    monkeypatch.setattr(discovery, "is_usable", usable)
    assert discovery.find_codex(str(shim)) == backend


def test_worker_retries_wrapper_when_native_app_initialization_fails(isolated, monkeypatch):
    from aiquota import worker as module
    from aiquota.app_server import AppServerError
    from aiquota.settings import AppSettings
    shim = make_exe(isolated / "Home" / ".local" / "bin" / "codex.cmd")
    backend = make_exe(isolated / "Local" / "OpenAI" / "Codex" / "bin" / "current" / "codex.exe")
    allow(monkeypatch, shim, backend)
    started, closed = [], []
    class Client:
        def __init__(self, executable, **_kwargs):
            self.executable = executable
            self.running = False
        def start(self):
            started.append(self.executable)
            if self.executable == backend:
                raise AppServerError("native handshake failed")
            self.running = True
        def close(self):
            closed.append(self.executable)
            self.running = False
    monkeypatch.setattr(module, "AppServerClient", Client)
    worker = module.QuotaWorker(AppSettings(codex_path=str(shim)), mock=True)
    try:
        worker._ensure_client()
        assert worker._client.executable == shim
        assert started == [backend, shim]
        assert closed == [backend]
    finally:
        worker._close_client()


def test_worker_falls_back_when_a_discovered_backend_cannot_initialize(isolated, monkeypatch):
    from aiquota import worker as module
    from aiquota.app_server import AppServerError
    from aiquota.settings import AppSettings
    first, second = Path("first.exe"), Path("second.exe")
    monkeypatch.setattr(module, "discover_codex_executable", lambda _hint, excluded: second if first in excluded else first)
    closed = []
    class Client:
        def __init__(self, executable, **_kwargs):
            self.executable = executable
            self.running = False
        def start(self):
            if self.executable == first:
                raise AppServerError("unsupported handshake")
            self.running = True
        def close(self):
            closed.append(self.executable)
            self.running = False
    monkeypatch.setattr(module, "AppServerClient", Client)
    worker = module.QuotaWorker(AppSettings(), mock=True)
    worker._ensure_client()
    assert worker._client.executable == second
    assert closed == [first]
    worker._close_client()
