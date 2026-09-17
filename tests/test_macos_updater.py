from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import threading

import pytest

pytestmark = pytest.mark.skipif(platform.system() != "Darwin", reason="macOS app bundle updater")

if platform.system() == "Darwin":
    from codexio import macos_updater as mac
from codexio import updates
from codexio.update_installer import _write_json, new_job_dir, read_state
from codexio.updates import UpdateCancelled, UpdateError, file_sha256


class Response(io.BytesIO):
    def geturl(self):
        return "https://release-assets.githubusercontent.com/test"


def manifest(**changes):
    data = dict(version="0.2.5", architecture=platform.machine(), size=12, sha256="a" * 64,
                url="https://github.com/Wujuhu/Codexio/releases/download/v0.2.5/Codexio.dmg")
    data.update(changes)
    return data


def test_mac_checks_its_own_release_manifest_and_never_uses_windows_asset(monkeypatch):
    requested = []
    def fetch(request, **kwargs):
        requested.append(request.full_url)
        return Response(json.dumps(manifest()).encode("utf-8"))
    monkeypatch.setattr(updates, "urlopen", fetch)
    release = mac.fetch_release("0.2.4")
    assert release.url.endswith("/v0.2.5/Codexio.dmg")
    assert requested == [mac.LATEST_MANIFEST]
    assert mac.fetch_release("0.2.5") is None


@pytest.mark.parametrize("changes", [
    {"architecture": "wrong-chip"},
    {"url": "https://github.com/Wujuhu/Codexio/releases/download/v0.2.5/Codexio.exe"},
    {"url": "https://github.com/Other/Codexio/releases/download/v0.2.5/Codexio.dmg"},
    {"sha256": "bad"},
])
def test_mac_rejects_wrong_platform_source_or_digest(monkeypatch, changes):
    monkeypatch.setattr(updates, "urlopen", lambda *a, **k: Response(json.dumps(manifest(**changes)).encode()))
    with pytest.raises(UpdateError):
        mac.fetch_release("0.2.4")


def test_dmg_download_uses_shared_size_and_hash_verification(tmp_path, monkeypatch):
    payload = b"DMG payload with no Windows header"
    release = updates.Release("0.2.5", manifest()["url"], hashlib.sha256(payload).hexdigest(), len(payload))
    monkeypatch.setattr(updates, "urlopen", lambda *a, **k: Response(payload))
    target = tmp_path / "package.bin"
    mac.download_release(release, target, threading.Event())
    assert target.read_bytes() == payload
    monkeypatch.setattr(updates, "urlopen", lambda *a, **k: Response(b"corrupt"))
    with pytest.raises(UpdateError):
        mac.download_release(release, tmp_path / "bad.bin", threading.Event())
    assert not (tmp_path / "bad.bin").exists()


def prepare(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEXIO_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    directory = new_job_dir()
    target = tmp_path / "安装 目录/Codexio.app"
    executable = target / "Contents/MacOS/Codexio"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"old executable")
    (directory / "package.bin").write_bytes(b"DMG test")
    job = dict(target=str(target), old_sha256=file_sha256(executable),
               sha256=file_sha256(directory / "package.bin"), version="0.2.5",
               parent_pid=os.getppid(), arguments=["--mock"])
    monkeypatch.setattr(mac, "_validate_bundle", lambda path, version=None: path / "Contents/MacOS/Codexio")
    def unpack(directory, pending, version):
        binary = pending / "Contents/MacOS/Codexio"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"new executable")
    monkeypatch.setattr(mac, "_prepare_bundle", unpack)
    monkeypatch.setattr(mac, "_wait_for_exit", lambda *a: None)
    return directory, target, executable, job


def test_install_preserves_old_bundle_and_requires_launch_ack(tmp_path, monkeypatch):
    directory, target, executable, job = prepare(tmp_path, monkeypatch)
    def restart(path, arguments, directory):
        assert path == target and arguments == ["--mock"]
        assert executable.read_bytes() == b"new executable"
        _write_json(directory / "ack.json", {"version": "0.2.5"})
        return type("Process", (), {"poll": lambda self: None})()
    monkeypatch.setattr(mac, "_restart", restart)
    mac._install(directory, job)
    assert read_state(directory)["state"] == "done"
    assert (directory / "previous.app/Contents/MacOS/Codexio").read_bytes() == b"old executable"
    assert not list(target.parent.glob("*.pending.app"))


def test_failed_launch_rolls_back_before_restarting_old_app(tmp_path, monkeypatch):
    directory, target, executable, job = prepare(tmp_path, monkeypatch)
    calls = []
    def restart(*args):
        calls.append(executable.read_bytes())
        if len(calls) == 1:
            raise OSError("launch failure")
    monkeypatch.setattr(mac, "_restart", restart)
    mac._install(directory, job)
    assert calls == [b"new executable", b"old executable"]
    assert read_state(directory)["state"] == "rolled_back"
    assert executable.read_bytes() == b"old executable"


@pytest.mark.parametrize("reason", ["cancel", "hash", "changed", "wait"])
def test_no_replacement_before_verified_normal_shutdown(tmp_path, monkeypatch, reason):
    directory, target, executable, job = prepare(tmp_path, monkeypatch)
    if reason == "cancel":
        (directory / "cancel").touch()
    elif reason == "hash":
        (directory / "package.bin").write_bytes(b"tampered")
    elif reason == "changed":
        job["old_sha256"] = "0" * 64
    else:
        def wait(*args):
            raise UpdateError("still running")
        monkeypatch.setattr(mac, "_wait_for_exit", wait)
    with pytest.raises(UpdateError):
        mac._install(directory, job)
    assert executable.read_bytes() == b"old executable"
    assert not list(target.parent.glob("*.pending.app"))


def test_native_wait_observes_commit_and_never_kills_parent(tmp_path):
    with pytest.raises(UpdateError, match="尚未退出"):
        mac._wait_for_exit(os.getpid(), tmp_path, timeout=0)
    (tmp_path / "cancel").touch()
    with pytest.raises(UpdateCancelled):
        mac._wait_for_exit(os.getpid(), tmp_path, timeout=0)


def test_mac_update_preference_migrates_old_disabled_config_and_persists_opt_out(tmp_path):
    from codexio.analytics_config import load_analytics_config, save_analytics_config
    path = tmp_path / "settings.json"
    path.write_text('{"auto_update": false}', encoding="utf-8")
    config = load_analytics_config(path)
    assert config["macos_auto_update"] is True and config["auto_update"] is False
    config["macos_auto_update"] = False
    save_analytics_config(config, path)
    assert load_analytics_config(path)["macos_auto_update"] is False
