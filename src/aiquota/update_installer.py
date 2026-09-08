"""Detached updater mode; no Qt and no installed Python are needed."""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from aiquota import __version__
from aiquota.settings import data_dir
from aiquota.updates import EXE_NAME, Release, UpdateCancelled, UpdateError, file_sha256, version_tuple


def updates_dir() -> Path:
    path = data_dir() / "updates"
    path.mkdir(exist_ok=True)
    return path.resolve()


def new_job_dir() -> Path:
    path = updates_dir() / uuid.uuid4().hex
    path.mkdir()
    return path


def _job_dir(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if resolved.parent != updates_dir() or not re.fullmatch(r"[0-9a-f]{32}", resolved.name) or path.is_symlink():
        raise UpdateError("更新暂存目录无效")
    return resolved


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _state(directory: Path, state: str, message: str = "") -> None:
    _write_json(directory / "state.json", {"state": state, "message": message, "time": time.time()})


def read_state(directory: Path) -> dict:
    try:
        value = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def spawn_independent(arguments: list[str], directory: Path, extra_env: dict | None = None):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    env.update(extra_env or {})
    options = {"cwd": str(directory), "env": env, "stdin": subprocess.DEVNULL,
               "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    reset_dll = os.name == "nt" and bool(getattr(sys, "frozen", False))
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    if reset_dll:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(arguments, **options)
    finally:
        if reset_dll:
            ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))


def launch_installer(directory: Path, release: Release, cancel: threading.Event, *,
                     executable: Path | None = None, target: Path | None = None,
                     parent_pid: int | None = None, arguments: list[str] | None = None) -> Path:
    directory = _job_dir(directory)
    if cancel.is_set():
        raise UpdateCancelled("已取消更新")
    executable = (executable or Path(sys.executable)).resolve(strict=True)
    target = (target or executable).resolve(strict=True)
    if target.name.lower() != EXE_NAME.lower() or target.is_symlink():
        raise UpdateError("请以 AIQuota.exe 文件名运行应用后更新")
    helper = directory / "AIQuotaUpdater.exe"
    shutil.copy2(executable, helper)
    _write_json(directory / "job.json", {
        "target": str(target), "old_sha256": file_sha256(target), "sha256": release.sha256,
        "version": release.version, "parent_pid": parent_pid or os.getpid(),
        "arguments": list(sys.argv[1:] if arguments is None else arguments),
    })
    process = spawn_independent([str(helper), "--apply-update", str(directory / "job.json")], directory)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if cancel.is_set():
            cancel_job(directory)
            raise UpdateCancelled("已取消更新")
        state = read_state(directory)
        if state.get("state") == "ready":
            return directory
        if state.get("state") == "failed" or process.poll() is not None:
            raise UpdateError(state.get("message") or "无法启动更新程序，当前版本继续运行")
        time.sleep(0.1)
    cancel_job(directory)
    raise UpdateError("更新准备超时，当前版本继续运行")


def cancel_job(directory: Path) -> None:
    (directory / "cancel").touch()


def commit_job(directory: Path) -> None:
    directory = _job_dir(directory)
    if read_state(directory).get("state") != "ready":
        raise UpdateError("更新准备状态已失效，请重新检查更新")
    (directory / "commit").touch()


@contextmanager
def _install_lock(target: Path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    import hashlib
    name = "Local\\AIQuota.Update." + hashlib.sha256(str(target).lower().encode("utf-8")).hexdigest()[:24]
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise UpdateError("无法获取更新锁")
    acquired = False
    try:
        acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
        if not acquired:
            raise UpdateError("另一个 AIQuota 实例正在更新，请稍后重试")
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def _wait_for_exit(pid: int, directory: Path, timeout: float = 90) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only; never terminate a process.
    if not handle and ctypes.get_last_error() != 87:
        raise UpdateError("无法等待应用正常退出")
    try:
        deadline = time.monotonic() + timeout
        while True:
            if (directory / "cancel").exists():
                raise UpdateCancelled("已取消更新")
            if not handle or kernel.WaitForSingleObject(handle, 0) == 0:
                if not (directory / "commit").exists():
                    raise UpdateCancelled("应用未确认安装，已保留当前版本")
                return
            if time.monotonic() >= deadline:
                raise UpdateError("应用尚未退出，已保留当前版本")
            time.sleep(0.1)
    finally:
        if handle:
            kernel.CloseHandle(handle)


def _replace_with_retry(source: Path, target: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise UpdateError("程序文件仍被占用，已保留旧版和下载文件")
            time.sleep(0.25)


def _restart(target: Path, arguments: list[str], directory: Path):
    return spawn_independent([str(target), *arguments], target.parent,
                             {"AIQUOTA_UPDATE_JOB": str(directory), "AIQUOTA_SKIP_UPDATE_ONCE": "1"})


def _install(directory: Path, job: dict) -> None:
    target = Path(job["target"])
    if not target.is_absolute() or target.name.lower() != EXE_NAME.lower() or target.is_symlink():
        raise UpdateError("更新目标无效")
    target = target.resolve(strict=True)
    version_tuple(job["version"])
    if not all(re.fullmatch(r"[0-9a-f]{64}", str(job.get(key, ""))) for key in ("sha256", "old_sha256")):
        raise UpdateError("更新校验信息无效")
    arguments = job.get("arguments", [])
    if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
        raise UpdateError("重启参数无效")
    pid = job.get("parent_pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        raise UpdateError("更新进程信息无效")
    package = directory / "package.bin"
    if file_sha256(package) != job["sha256"]:
        raise UpdateError("下载文件被修改，已取消安装")
    with package.open("rb") as check:
        if check.read(2) != b"MZ":
            raise UpdateError("下载文件不是 Windows 程序")
    pending = target.parent / (".AIQuota-" + directory.name + ".pending")
    # All replacement files are explicit siblings of the resolved executable.
    # Copy first so replacement remains atomic even when LOCALAPPDATA is on another drive.
    backup = directory / "previous.bin"
    parent_exited = False
    replaced = False
    process = None
    with _install_lock(target):
        try:
            if file_sha256(target) != job["old_sha256"]:
                raise UpdateError("程序文件已更新或被修改，请重新检查更新")
            with pending.open("xb") as output, package.open("rb") as source:
                shutil.copyfileobj(source, output)
            if file_sha256(pending) != job["sha256"]:
                raise UpdateError("暂存文件校验失败")
            shutil.copy2(target, backup)
            if file_sha256(backup) != job["old_sha256"]:
                raise UpdateError("旧版备份校验失败")
            _state(directory, "ready")
            _wait_for_exit(pid, directory)
            parent_exited = True
            if file_sha256(target) != job["old_sha256"]:
                raise UpdateError("程序文件已变化，停止替换")
            _replace_with_retry(pending, target)
            replaced = True
            process = _restart(target, arguments, directory)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                ack_path = directory / "ack.json"
                if ack_path.exists():
                    ack = json.loads(ack_path.read_text(encoding="utf-8"))
                    if ack.get("version") == job["version"]:
                        _state(directory, "done", "已更新至 " + job["version"])
                        return
                if process.poll() is not None:
                    raise UpdateError("新版启动失败，正在恢复旧版")
                time.sleep(0.2)
            _state(directory, "unconfirmed", "已替换并启动新版；启动确认超时，旧版备份已保留")
        except Exception as exc:
            if parent_exited:
                # Never roll back over a running new instance after a status-file error.
                if process is not None and process.poll() is None:
                    raise
                if replaced:
                    shutil.copy2(backup, pending)
                    _replace_with_retry(pending, target)
                _restart(target, arguments, directory)
                _state(directory, "rolled_back", str(exc))
                return
            raise
        finally:
            pending.unlink(missing_ok=True)


def run_update_job(job_path: str) -> int:
    directory = None
    try:
        path = Path(job_path)
        directory = _job_dir(path.parent)
        if path.name != "job.json" or (getattr(sys, "frozen", False) and Path(sys.executable).resolve().parent != directory):
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


def acknowledge_update() -> str:
    raw = os.environ.pop("AIQUOTA_UPDATE_JOB", "")
    if not raw:
        return ""
    try:
        directory = _job_dir(Path(raw))
        _write_json(directory / "ack.json", {"version": __version__, "pid": os.getpid()})
        state = read_state(directory)
        if state.get("state") == "rolled_back":
            return "更新失败，已恢复旧版：" + str(state.get("message", ""))
        return "已更新至 " + __version__
    except (OSError, ValueError, UpdateError):
        return ""


def cleanup_updates() -> None:
    root = updates_dir()
    jobs = sorted((path for path in root.iterdir() if re.fullmatch(r"[0-9a-f]{32}", path.name)
                   and path.is_dir() and not path.is_symlink()), key=lambda path: path.stat().st_mtime, reverse=True)
    backups_kept = 0
    for path in jobs:
        if path.resolve().parent != root:
            continue
        state = read_state(path).get("state")
        age = time.time() - path.stat().st_mtime
        if age < 120 or state not in ("done", "rolled_back", "cancelled", "failed", "unconfirmed"):
            continue
        keep_backup = (path / "previous.bin").exists() and backups_kept < 2
        backups_kept += int(keep_backup)
        for name in ("package.bin", "package.part", "AIQuotaUpdater.exe", "previous.bin"):
            if name == "previous.bin" and keep_backup:
                continue
            try:
                (path / name).unlink(missing_ok=True)
            except OSError:
                pass
