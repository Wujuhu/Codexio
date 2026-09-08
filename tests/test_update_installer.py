from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aiquota import update_installer as installer
from aiquota.updates import UpdateCancelled, UpdateError, file_sha256


def prepare(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    directory = installer.new_job_dir()
    target_dir = tmp_path / "程序 空格" / "app"
    target_dir.mkdir(parents=True)
    target = target_dir / "AIQuota.exe"
    target.write_bytes(b"MZold executable")
    package = directory / "package.bin"
    package.write_bytes(b"MZnew executable")
    job = {"target": str(target), "old_sha256": file_sha256(target), "sha256": file_sha256(package),
           "version": "0.1.2", "parent_pid": os.getppid(), "arguments": ["--mock"]}
    monkeypatch.setattr(installer, "_wait_for_exit", lambda *_args: None)
    return directory, target, job


def test_install_replaces_only_target_and_restarts_with_original_arguments(tmp_path, monkeypatch):
    directory, target, job = prepare(tmp_path, monkeypatch)
    restarts = []
    def restart(path, arguments, job_dir):
        restarts.append((path, arguments))
        assert path.read_bytes() == b"MZnew executable"
        installer._write_json(job_dir / "ack.json", {"version": "0.1.2"})
        return type("Process", (), {"poll": lambda _self: None})()
    monkeypatch.setattr(installer, "_restart", restart)
    installer._install(directory, job)
    assert restarts == [(target, ["--mock"])]
    assert (directory / "previous.bin").read_bytes() == b"MZold executable"
    assert installer.read_state(directory)["state"] == "done"
    assert list(target.parent.iterdir()) == [target]


def test_failed_new_program_launch_restores_old_binary_before_restart(tmp_path, monkeypatch):
    directory, target, job = prepare(tmp_path, monkeypatch)
    contents = []
    def restart(path, *_args):
        contents.append(path.read_bytes())
        if len(contents) == 1:
            raise OSError("launch failed")
        return type("Process", (), {"poll": lambda _self: None})()
    monkeypatch.setattr(installer, "_restart", restart)
    installer._install(directory, job)
    assert contents == [b"MZnew executable", b"MZold executable"]
    assert target.read_bytes() == b"MZold executable"
    assert installer.read_state(directory)["state"] == "rolled_back"
    assert list(target.parent.iterdir()) == [target]


def test_tampering_after_download_never_exits_or_replaces_current_app(tmp_path, monkeypatch):
    directory, target, job = prepare(tmp_path, monkeypatch)
    (directory / "package.bin").write_bytes(b"MZtampered")
    with pytest.raises(UpdateError, match="被修改"):
        installer._install(directory, job)
    assert target.read_bytes() == b"MZold executable"
    assert installer.read_state(directory).get("state") != "ready"


def test_changed_current_exe_is_not_overwritten(tmp_path, monkeypatch):
    directory, target, job = prepare(tmp_path, monkeypatch)
    target.write_bytes(b"MZsomeone updated the app already")
    with pytest.raises(UpdateError, match="已更新或被修改"):
        installer._install(directory, job)
    assert target.read_bytes() == b"MZsomeone updated the app already"


def test_update_job_must_be_in_its_own_staging_area(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))
    outside = tmp_path / ("a" * 32)
    outside.mkdir()
    with pytest.raises(UpdateError):
        installer._job_dir(outside)


def test_native_wait_requires_commit_and_does_not_terminate_parent(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"],
                               creationflags=subprocess.CREATE_NO_WINDOW)
    (tmp_path / "cancel").touch()
    try:
        with pytest.raises(UpdateCancelled):
            installer._wait_for_exit(process.pid, tmp_path, timeout=0.1)
        assert process.poll() is None
    finally:
        process.wait(timeout=5)
    (tmp_path / "cancel").unlink()
    with pytest.raises(UpdateCancelled):
        installer._wait_for_exit(process.pid, tmp_path, timeout=1)
    (tmp_path / "commit").touch()
    installer._wait_for_exit(process.pid, tmp_path, timeout=1)


def test_restart_gets_fresh_pyinstaller_environment(tmp_path, monkeypatch):
    captured = {}
    def popen(arguments, **options):
        captured.update(arguments=arguments, **options)
        return object()
    monkeypatch.setattr(installer.subprocess, "Popen", popen)
    installer._restart(tmp_path / "AIQuota.exe", ["--mock"], tmp_path)
    assert captured["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert captured["env"]["AIQUOTA_SKIP_UPDATE_ONCE"] == "1"
    assert captured["arguments"] == [str(tmp_path / "AIQuota.exe"), "--mock"]
    assert captured["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_auto_update_can_be_cancelled_after_download_before_restart(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from aiquota.update_manager import UpdateManager
    app = QApplication.instance() or QApplication([])
    directory, _target, _job = prepare(tmp_path, monkeypatch)
    installer._state(directory, "ready")
    restarts = []
    manager = UpdateManager(app, lambda: restarts.append(True), available=True)
    manager._busy = True
    manager._handle_event(("ready", directory))
    manager.set_enabled(False)
    manager._commit_install()
    assert not restarts and not manager._busy
    assert (directory / "cancel").exists()
    assert not (directory / "commit").exists()
    manager.stop()


def test_install_callback_occurs_only_after_ready_and_commit(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from aiquota.update_manager import UpdateManager
    app = QApplication.instance() or QApplication([])
    directory, _target, _job = prepare(tmp_path, monkeypatch)
    installer._state(directory, "ready")
    restarts = []
    manager = UpdateManager(app, lambda: restarts.append((directory / "commit").exists()), available=True)
    manager._busy = True
    manager._handle_event(("ready", directory))
    assert not restarts
    manager._commit_install()
    assert restarts == [True]
    manager.stop()
    assert not (directory / "cancel").exists()
