"""GitHub DMG updates using the shared download and shutdown protocol."""
from __future__ import annotations

import fcntl
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from codexio import __version__, updates
from codexio.update_installer import (
    _job_dir, _state, _write_json, cancel_job, read_state, spawn_independent, updates_dir,
)
from codexio.updates import Release, UpdateCancelled, UpdateError, file_sha256, version_tuple

DMG_NAME = "Codexio.dmg"
MANIFEST_NAME = "latest.json"
LATEST_MANIFEST = updates.LATEST_MANIFEST
BUNDLE_ID = "com.wujuhu.codexio"


def fetch_release(current_version=__version__):
    return updates.fetch_release(current_version, manifest_url=LATEST_MANIFEST,
                                 asset_name=DMG_NAME, architecture=platform.machine(), manifest_key="macos")


def download_release(release, target, cancel, progress=lambda _value: None):
    return updates.download_release(release, target, cancel, progress, windows_executable=False)


def _run(*arguments):
    try:
        return subprocess.run(list(map(str, arguments)), check=True, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=120)
    except (subprocess.SubprocessError, OSError) as exc:
        raise UpdateError("Mac 更新文件校验或准备失败，已保留当前版本") from exc


def _validate_bundle(bundle: Path, version=None):
    if bundle.is_symlink() or not bundle.is_dir():
        raise UpdateError("Mac 应用包无效")
    executable = bundle / "Contents/MacOS/Codexio"
    with (bundle / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    actual_version = info.get("CFBundleShortVersionString")
    version_tuple(actual_version)
    if (info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != "Codexio"
            or info.get("CFBundleVersion") != actual_version
            or (version is not None and actual_version != version)
            or not executable.resolve().is_relative_to(bundle.resolve())):
        raise UpdateError("Mac 应用标识或版本与更新清单不一致")
    _run("/usr/bin/codesign", "--verify", "--deep", "--strict", bundle)
    _run("/usr/bin/lipo", "-verify_arch", platform.machine(), executable)
    return executable


def launch_installer(directory: Path, release: Release, cancel: threading.Event, *,
                     executable=None, parent_pid=None, arguments=None):
    directory = _job_dir(directory)
    executable = Path(executable or sys.executable).resolve(strict=True)
    target = executable.parents[2]
    if target.suffix != ".app" or executable != target / "Contents/MacOS/Codexio":
        raise UpdateError("请从安装好的 Codexio.app 中更新")
    _validate_bundle(target)
    if cancel.is_set():
        raise UpdateCancelled("已取消更新")
    helper = directory / "CodexioUpdater.app"
    _run("/usr/bin/ditto", target, helper)
    _write_json(directory / "job.json", dict(
        target=str(target), old_sha256=file_sha256(executable), sha256=release.sha256,
        version=release.version, parent_pid=parent_pid or os.getpid(),
        arguments=list(sys.argv[1:] if arguments is None else arguments)))
    process = spawn_independent([str(helper / "Contents/MacOS/Codexio"), "--apply-mac-update",
                                 str(directory / "job.json")], directory)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if cancel.is_set():
            cancel_job(directory)
            raise UpdateCancelled("已取消更新")
        state = read_state(directory)
        if state.get("state") == "ready":
            return directory
        if state.get("state") == "failed" or process.poll() is not None:
            raise UpdateError(state.get("message") or "无法启动 Mac 更新程序")
        time.sleep(0.1)
    cancel_job(directory)
    raise UpdateError("更新准备超时，当前版本继续运行")


def _prepare_bundle(directory, pending, version):
    mount = directory / "mount"
    mount.mkdir()
    mounted = False
    try:
        _run("/usr/bin/hdiutil", "attach", directory / "package.bin", "-readonly", "-nobrowse",
             "-mountpoint", mount, "-plist")
        mounted = True
        source = mount / "Codexio.app"
        _validate_bundle(source, version)
        _run("/usr/bin/ditto", source, pending)
        _validate_bundle(pending, version)
    finally:
        if mounted:
            _run("/usr/bin/hdiutil", "detach", mount)
        mount.rmdir()


def _wait_for_exit(pid, directory, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        if (directory / "cancel").exists():
            raise UpdateCancelled("已取消更新")
        try:
            os.kill(pid, 0)  # Observe only; the app exits through its normal shutdown.
        except ProcessLookupError:
            if not (directory / "commit").exists():
                raise UpdateCancelled("应用未确认安装，已保留当前版本")
            return
        if time.monotonic() >= deadline:
            raise UpdateError("应用尚未退出，已保留当前版本")
        time.sleep(0.1)


def _restart(target, arguments, directory):
    return spawn_independent([str(target / "Contents/MacOS/Codexio"), *arguments], target.parent,
                             {"CODEXIO_UPDATE_JOB": str(directory), "CODEXIO_SKIP_UPDATE_ONCE": "1"})


def _install(directory, job):
    target = Path(job["target"])
    if not target.is_absolute() or target.suffix != ".app" or target.is_symlink():
        raise UpdateError("Mac 更新目标无效")
    target = target.resolve(strict=True)
    executable = _validate_bundle(target)
    version_tuple(job["version"])
    if not all(re.fullmatch(r"[0-9a-f]{64}", str(job.get(key, ""))) for key in ("sha256", "old_sha256")):
        raise UpdateError("更新校验信息无效")
    pid = job.get("parent_pid")
    arguments = job.get("arguments", [])
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        raise UpdateError("更新进程信息无效")
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise UpdateError("重启参数无效")
    if file_sha256(directory / "package.bin") != job["sha256"]:
        raise UpdateError("下载文件被修改，已取消安装")
    pending = target.parent / (".Codexio-" + directory.name + ".pending.app")
    backup = target.parent / (".Codexio-" + directory.name + ".previous.app")
    if pending.exists() or backup.exists():
        raise UpdateError("更新暂存文件已存在，请重新检查更新")
    process = None
    moved_old = False
    parent_exited = False
    with (target.parent / ".Codexio-update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError("另一个实例正在更新，请稍后重试") from exc
        try:
            if file_sha256(executable) != job["old_sha256"]:
                raise UpdateError("当前应用已更新或被修改")
            _prepare_bundle(directory, pending, job["version"])
            if (directory / "cancel").exists():
                raise UpdateCancelled("已取消更新")
            _state(directory, "ready")
            _wait_for_exit(pid, directory)
            parent_exited = True
            if file_sha256(_validate_bundle(target)) != job["old_sha256"]:
                raise UpdateError("当前应用已变化，停止替换")
            target.rename(backup)
            moved_old = True
            pending.rename(target)
            process = _restart(target, arguments, directory)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                ack = directory / "ack.json"
                if ack.exists() and json.loads(ack.read_text(encoding="utf-8")).get("version") == job["version"]:
                    shutil.move(str(backup), directory / "previous.app")
                    _state(directory, "done", "已更新至 " + job["version"])
                    return
                if process.poll() is not None:
                    raise UpdateError("新版启动失败，正在恢复旧版")
                time.sleep(0.2)
            _state(directory, "unconfirmed", "新版启动确认超时，旧版备份已保留在 " + str(backup))
        except Exception as exc:
            # A running new app must never be replaced by a rollback.
            if parent_exited and (process is None or process.poll() is not None):
                if moved_old:
                    if target.exists():
                        shutil.rmtree(target)
                    backup.rename(target)
                _state(directory, "rolled_back", str(exc))
                _restart(target, arguments, directory)
                return
            raise
        finally:
            if pending.exists():
                shutil.rmtree(pending)


def run_update_job(job_path):
    directory = None
    try:
        path = Path(job_path)
        directory = _job_dir(path.parent)
        helper = directory / "CodexioUpdater.app/Contents/MacOS/Codexio"
        if path.name != "job.json" or (getattr(sys, "frozen", False) and Path(sys.executable).resolve() != helper):
            raise UpdateError("更新任务位置无效")
        payload = path.read_text(encoding="utf-8")
        if len(payload) > 32000:
            raise UpdateError("更新任务过大")
        _install(directory, json.loads(payload))
        return 0
    except UpdateCancelled as exc:
        if directory:
            _state(directory, "cancelled", str(exc))
        return 0
    except Exception as exc:
        if directory:
            _state(directory, "failed", str(exc))
        return 1


def cleanup_updates():
    jobs = sorted((path for path in updates_dir().iterdir() if re.fullmatch(r"[0-9a-f]{32}", path.name)
                   and path.is_dir() and not path.is_symlink()), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in jobs[2:]:
        if (time.time() - path.stat().st_mtime > 86400
                and read_state(path).get("state") in ("done", "rolled_back", "cancelled", "failed")):
            shutil.rmtree(path)
