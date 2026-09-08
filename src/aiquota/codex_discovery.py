"""Find a usable Codex backend without depending on npm or a fixed App version."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Iterator

_PROBES: dict = {}
_SKIP_DIRS = {"cache", "code cache", "gpucache", "logs", "crashpad", ".git", "sessions"}


def _path(value: str) -> Path:
    return Path(os.path.expandvars(value.strip().strip('"'))).expanduser()


def _recent(paths) -> list[Path]:
    def stamp(path):
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0
    return sorted(paths, key=stamp, reverse=True)


def _scan(root: Path) -> Iterator[Path]:
    """Scan only an installation root, bounded in depth and number of directories."""
    if root.parent == root:
        return
    queue = deque([(root, 0)])
    found = []
    visited = 0
    while queue and visited < 256:
        directory, depth = queue.popleft()
        visited += 1
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        children = []
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file() and entry.name.lower() == "codex.exe":
                    found.append(entry)
                elif entry.is_dir() and depth < 8 and entry.name.lower() not in _SKIP_DIRS:
                    # Do not follow directory junctions into unrelated trees.
                    if not (getattr(entry.lstat(), "st_file_attributes", 0) & 0x400):
                        children.append(entry)
            except OSError:
                continue
        queue.extend((child, depth + 1) for child in _recent(children))
    yield from _recent(found)


def _npm_root() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "npm"


def _hint_candidates(hint: str) -> Iterator[Path]:
    try:
        path = _path(hint)
        if path.is_dir():
            for name in ("codex.exe", "codex.cmd", "codex"):
                yield path / name
            yield from _scan(path)
        else:
            if path.suffix.lower() in (".cmd", ".bat"):
                yield path.with_suffix(".exe")
                # A ~/.local/bin shim can forward to the standard npm install.
                # Search these bounded package roots without interpreting shell code.
                for root in dict.fromkeys((path.parent, _npm_root())):
                    yield from _scan(root / "node_modules" / "@openai" / "codex")
            yield path
            # A manually saved App version path may have disappeared after an update.
            for parent in list(path.parents)[:4]:
                if parent.name.lower() in ("bin", "resources"):
                    yield from _scan(parent)
                    break
    except (OSError, ValueError, RuntimeError):
        return


def _standard_app_roots() -> Iterator[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    yield local / "OpenAI" / "Codex"
    yield local / "Programs" / "OpenAI" / "Codex"
    yield local / "Programs" / "Codex"
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(variable)
        if value:
            yield Path(value) / "OpenAI" / "Codex"
            yield Path(value) / "Codex"


def _registry_app_roots() -> Iterator[Path]:
    if os.name != "nt":
        return
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view) as entries:
                    count = winreg.QueryInfoKey(entries)[0]
                    for index in range(count):
                        try:
                            with winreg.OpenKey(entries, winreg.EnumKey(entries, index)) as item:
                                name = str(winreg.QueryValueEx(item, "DisplayName")[0]).lower()
                                if "codex" not in name:
                                    continue
                                for field in ("InstallLocation", "DisplayIcon"):
                                    try:
                                        value = str(winreg.QueryValueEx(item, field)[0]).strip()
                                        if value:
                                            path = _path(re.sub(r",\s*-?\d+$", "", value))
                                            yield path.parent if field == "DisplayIcon" else path
                                    except OSError:
                                        pass
                        except OSError:
                            continue
            except OSError:
                continue


def _run_probe(arguments: list[str], timeout: float):
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
               "timeout": timeout, "cwd": str(Path.home())}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(arguments, **options)


def _windows_app_locations() -> tuple[list[Path], list[Path]]:
    """Ask Windows for current MSIX installations, including custom install drives."""
    if os.name != "nt":
        return [], []
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = r'''
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$roots = @(Get-AppxPackage -Name '*Codex*' | Sort-Object Version -Descending | Select-Object -ExpandProperty InstallLocation)
$running = @(Get-CimInstance Win32_Process -Filter "Name='codex.exe'" | Select-Object -ExpandProperty ExecutablePath -Unique)
@{ roots = $roots; running = $running } | ConvertTo-Json -Compress
'''
    try:
        result = _run_probe([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script], 10)
        data = json.loads(result.stdout.decode("utf-8-sig"))
        return ([Path(value) for value in data.get("roots", []) if isinstance(value, str) and value],
                [Path(value) for value in data.get("running", []) if isinstance(value, str) and value])
    except (OSError, ValueError, subprocess.SubprocessError):
        return [], []


def candidates(explicit: str | None = None) -> Iterator[Path]:
    for hint in (explicit, os.environ.get("CODEX_CLI_PATH")):
        if hint:
            yield from _hint_candidates(hint)
    # Keep existing CLI setups working; npm itself is not required.
    for name in ("codex.exe", "codex.cmd", "codex"):
        found = shutil.which(name)
        if found:
            yield from _hint_candidates(found)
    for root in _standard_app_roots():
        yield from _scan(root)
    for root in _registry_app_roots():
        yield from _scan(root)
    roots, running = _windows_app_locations()
    for path in running:
        yield path
    for root in roots:
        yield from _scan(root)
    local_bin = Path.home() / ".local" / "bin"
    roaming = _npm_root()
    for root in (local_bin, roaming):
        for name in ("codex.exe", "codex.cmd", "codex"):
            yield from _hint_candidates(str(root / name))


def is_usable(path: Path) -> bool:
    try:
        info = path.stat()
        if not path.is_file() or info.st_size == 0:
            return False
        # Never launch the Electron desktop UI while probing for its backend.
        if (path.parent / "resources" / "app.asar").exists() or (path.parent / "chrome_100_percent.pak").exists():
            return False
        key = (str(path).casefold(), info.st_mtime_ns, info.st_size)
        cached = _PROBES.get(key)
        if cached and time.monotonic() - cached[0] < (60 if cached[1] else 5):
            return cached[1]
        result = _run_probe([str(path), "--version"], 4)
        valid = result.returncode == 0 and re.search(rb"(?im)^\s*codex-cli\s+\S+", result.stdout) is not None
        if valid:
            result = _run_probe([str(path), "app-server", "--help"], 4)
            text = result.stdout + result.stderr
            valid = result.returncode == 0 and b"app-server" in text and b"--listen" in text
        if len(_PROBES) > 128:
            _PROBES.clear()
        _PROBES[key] = (time.monotonic(), valid)
        return valid
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def find_codex(explicit: str | None = None, excluded=()) -> Path | None:
    seen = {str(path).casefold() for path in excluded}
    wrappers = []
    for candidate in candidates(explicit):
        try:
            candidate = candidate.resolve()
        except (OSError, RuntimeError):
            continue
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        # A saved shim may only forward to another npm shim. Prefer any usable
        # native backend (including the one bundled with Codex App) before
        # starting a persistent cmd/Node process tree. Keep shims as a fallback
        # for installations whose native executable cannot be used directly.
        if candidate.suffix.lower() in (".cmd", ".bat"):
            wrappers.append(candidate)
            continue
        if is_usable(candidate):
            return candidate
    for candidate in wrappers:
        if is_usable(candidate):
            return candidate
    return None
