"""Install a launched development bundle at the one stable WidgetKit host path."""
from __future__ import annotations

import hashlib
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

from codexio.settings import data_dir


CANONICAL_APP = Path("/Applications/Codexio.app")
WIDGET_RELATIVE = Path("Contents/PlugIns/CodexioWidget.appex")
TAKEOVER_PREFIX = ".Codexio-takeover-"


def _info(bundle: Path):
    if bundle.is_symlink() or not bundle.is_dir():
        raise RuntimeError("Codexio APP 路径无效")
    try:
        with (bundle / "Contents/Info.plist").open("rb") as stream:
            app = plistlib.load(stream)
        with (bundle / WIDGET_RELATIVE / "Contents/Info.plist").open("rb") as stream:
            widget = plistlib.load(stream)
        version = tuple(int(part) for part in str(app["CFBundleShortVersionString"]).split("."))
        build = int(widget["CFBundleVersion"])
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException) as exc:
        raise RuntimeError("Codexio APP 或小组件信息无效") from exc
    if (app.get("CFBundleIdentifier") != "com.wujuhu.codexio"
            or widget.get("CFBundleIdentifier") != "com.wujuhu.codexio.widget"):
        raise RuntimeError("Codexio APP 或小组件标识无效")
    return version, build


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _same_build(first: Path, second: Path) -> bool:
    try:
        return all(_digest(first / relative) == _digest(second / relative) for relative in (
            Path("Contents/MacOS/Codexio"), WIDGET_RELATIVE / "Contents/MacOS/CodexioWidget"))
    except OSError:
        return False


def _verify(bundle: Path) -> None:
    _info(bundle)
    result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("Codexio APP 签名校验失败")


def _stop_old_agent() -> None:
    label = "com.wujuhu.codexio.widget-refresh"
    agent = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    domain = "gui/" + str(os.getuid())
    subprocess.run(["/bin/launchctl", "bootout", domain, str(agent)],
                   capture_output=True, timeout=10, check=False)
    agent.unlink(missing_ok=True)


def _stop_competing_processes() -> None:
    processes = []
    for process in psutil.process_iter(["pid", "exe", "cmdline"]):
        if process.pid == os.getpid():
            continue
        executable = process.info.get("exe")
        arguments = process.info.get("cmdline") or []
        try:
            path = Path(executable).resolve() if executable else None
        except (OSError, RuntimeError):
            path = None
        if path is None:
            continue
        is_widget = str(path).endswith("/Contents/PlugIns/CodexioWidget.appex/Contents/MacOS/CodexioWidget")
        is_host = str(path).endswith("/Codexio.app/Contents/MacOS/Codexio")
        protected_helper = any(value in arguments for value in ("--upstream-proxy", "--apply-mac-update"))
        if is_widget or is_host and not protected_helper:
            processes.append(process)
    for process in processes:
        try:
            process.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(processes, timeout=3)
    for process in alive:
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=2)


def _launch(bundle: Path):
    executable = bundle / "Contents/MacOS/Codexio"
    environment = dict(os.environ)
    environment["CODEXIO_CANONICAL_TAKEOVER"] = "1"
    environment["CODEXIO_SKIP_UPDATE_ONCE"] = "1"
    return subprocess.Popen([str(executable), *sys.argv[1:]], cwd=str(bundle.parent), env=environment,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True, start_new_session=True)


def _launch_checked(bundle: Path):
    process = _launch(bundle)
    time.sleep(0.5)
    if process.poll() is not None:
        raise RuntimeError("接管后的 Codexio 未能启动")
    return process


def take_over_canonical_app() -> bool:
    """Return true after handing execution to the canonical installed copy."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return False
    source = Path(sys.executable).resolve().parents[2]
    target = CANONICAL_APP
    if source.name != "Codexio.app":
        raise RuntimeError("请从完整的 Codexio.app 启动")
    if source == target:
        return False
    source_rank = _info(source)
    if target.exists():
        try:
            target_rank = _info(target)
        except RuntimeError:
            target_rank = ()
        if target_rank > source_rank or target_rank == source_rank and _same_build(source, target):
            _stop_old_agent()
            _stop_competing_processes()
            (data_dir() / "widget_install_state.json").unlink(missing_ok=True)
            _launch_checked(target)
            return True

    nonce = secrets.token_hex(8)
    pending = target.parent / (TAKEOVER_PREFIX + nonce + ".pending")
    backup = target.parent / (TAKEOVER_PREFIX + nonce + ".previous")
    moved_old = False
    try:
        subprocess.run(["/usr/bin/ditto", "--norsrc", "--noextattr", str(source), str(pending)],
                       capture_output=True, timeout=120, check=True)
        _verify(pending)
        if _info(pending) != source_rank:
            raise RuntimeError("接管副本版本不一致")
        _stop_old_agent()
        _stop_competing_processes()
        if target.exists():
            target.rename(backup)
            moved_old = True
        pending.rename(target)
        (data_dir() / "widget_install_state.json").unlink(missing_ok=True)
        _launch_checked(target)
        return True
    except (RuntimeError, OSError, subprocess.SubprocessError, psutil.Error) as exc:
        if not isinstance(exc, RuntimeError):
            exc = RuntimeError("无法将最新 Codexio 接管到应用程序目录")
        if target.exists() and _same_build(source, target):
            shutil.rmtree(target)
        if moved_old and backup.exists():
            backup.rename(target)
            _launch(target)
        raise exc
    finally:
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)


def cleanup_takeover_backups() -> None:
    """Remove only backups created by a confirmed Codexio canonical takeover."""
    if Path(sys.executable).resolve().parents[2] != CANONICAL_APP:
        return
    for path in CANONICAL_APP.parent.iterdir():
        if (path.is_dir() and not path.is_symlink()
                and re.fullmatch(re.escape(TAKEOVER_PREFIX) + r"[0-9a-f]{16}\.previous", path.name)):
            shutil.rmtree(path, ignore_errors=True)
