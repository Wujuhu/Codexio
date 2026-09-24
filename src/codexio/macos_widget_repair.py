"""Recover WidgetKit registration after replacing the installed macOS APP."""
from __future__ import annotations

import json
import os
import plistlib
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from codexio.logging_setup import get_logger
from codexio.settings import data_dir

WIDGET_ID = "com.wujuhu.codexio.widget"
WIDGET_EXECUTABLE = "Contents/PlugIns/CodexioWidget.appex/Contents/MacOS/CodexioWidget"


def _run(*arguments):
    return subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", timeout=8, check=False)


def _registered_extensions():
    result = _run("pluginkit", "-m", "-A", "-D", "-v", "-i", WIDGET_ID)
    if result.returncode:
        raise OSError("无法读取系统小组件注册记录")
    paths = set()
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or WIDGET_ID not in fields[0]:
            continue
        path = Path(fields[-1].strip())
        if path.name == "CodexioWidget.appex" and path.parent.name == "PlugIns":
            paths.add(path)
    return paths


def _registered_version(extension: Path):
    result = _run("pluginkit", "-m", "-A", "-D", "-v", "-i", WIDGET_ID)
    if result.returncode:
        return None
    for line in result.stdout.splitlines():
        match = re.search(re.escape(WIDGET_ID) + r"\(([^)]+)\)", line)
        if match and Path(line.split("\t")[-1].strip()) == extension:
            return match.group(1)
    return None


def _installed_identity(bundle: Path, extension: Path):
    app_stat = (bundle / "Contents/MacOS/Codexio").stat()
    widget_stat = (extension / "Contents/MacOS/CodexioWidget").stat()
    return {"bundle": str(bundle),
            "app": [app_stat.st_dev, app_stat.st_ino, app_stat.st_size, app_stat.st_mtime_ns],
            "widget": [widget_stat.st_dev, widget_stat.st_ino, widget_stat.st_size, widget_stat.st_mtime_ns]}


def _old_widget_processes(current: Path, *, replaced: bool):
    result = _run("ps", "-u", str(os.getuid()), "-o", "pid=,comm=")
    if result.returncode:
        return []
    processes = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        executable = fields[1]
        if executable.endswith("/" + WIDGET_EXECUTABLE) and (replaced or executable != str(current)):
            processes.append(int(fields[0]))
    return processes


def _retire_processes(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except ProcessLookupError:
                pass
        if not alive:
            return True
        time.sleep(0.05)
    return False


def repair_installed_widget() -> bool:
    """Make the frozen APP the sole registered host for Codexio's widget."""
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return False
    bundle = Path(sys.executable).resolve().parents[2]
    extension = bundle / "Contents/PlugIns/CodexioWidget.appex"
    if bundle.name != "Codexio.app" or not extension.is_dir():
        return False
    logger = get_logger("widget")
    marker = data_dir() / "widget_install_state.json"
    try:
        identity = _installed_identity(bundle, extension)
        with (extension / "Contents/Info.plist").open("rb") as stream:
            expected_version = str(plistlib.load(stream).get("CFBundleShortVersionString") or "")
        try:
            previous = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
        replaced = previous != identity
        registered = _registered_extensions()
        stale = registered - {extension}
        if not replaced and not stale and extension in registered:
            from codexio.macos_widget_snapshot import reload_widget
            return reload_widget()
        registration_ok = True
        # Register and elect the current extension before removing the old host,
        # so a failed registration never leaves existing widgets without a provider.
        if (replaced or extension not in registered) and _run("pluginkit", "-a", str(extension)).returncode:
            registration_ok = False
            logger.warning("当前小组件扩展暂未注册")
        if registration_ok and _run("pluginkit", "-e", "use", "-i", WIDGET_ID).returncode:
            registration_ok = False
            logger.warning("当前小组件扩展暂未选用")
        current_registered = extension in _registered_extensions() if registration_ok else False
        if current_registered:
            for path in stale:
                if _run("pluginkit", "-r", str(path)).returncode:
                    registration_ok = False
                    logger.warning("旧小组件扩展暂未注销: %s", path)
        else:
            registration_ok = False
        registered_after = _registered_extensions() if registration_ok else set()
        registration_ok = (registration_ok and registered_after == {extension}
                           and _registered_version(extension) == expected_version)
        pids = (_old_widget_processes(extension / "Contents/MacOS/CodexioWidget",
                                      replaced=replaced or bool(stale)) if registration_ok else [])
        retired = _retire_processes(pids)
        from codexio.macos_widget_snapshot import reload_widget
        reloaded = reload_widget()
        if registration_ok and retired and reloaded:
            temporary = marker.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(identity, separators=(",", ":")), encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(marker)
            return True
        else:
            logger.warning("小组件注册或旧进程仍待恢复，下次启动将重试")
            return False
    except (OSError, subprocess.TimeoutExpired, ValueError, plistlib.InvalidFileException):
        logger.exception("首次启动时修复小组件注册失败，下次启动将重试")
        return False
