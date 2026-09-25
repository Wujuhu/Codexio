"""Identify and restart the official desktop client, never unrelated Codex CLIs."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

import psutil

from codexio.upstream_config import UpstreamError
from codexio.process_env import external_environment


def identity(process):
    return {"pid": process.pid, "created": process.create_time()}


def alive(value):
    try:
        process = psutil.Process(int(value["pid"]))
        return abs(process.create_time() - float(value["created"])) < .01 and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, KeyError, ValueError, TypeError):
        return False


def desktop_target(exe, args=(), platform=None):
    platform = platform or sys.platform
    path = Path(exe)
    if platform == "darwin":
        if path.name not in ("ChatGPT", "Codex") or path.parent.name != "MacOS":
            return None
        bundle = path.parent.parent.parent
        try:
            with (bundle / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            if info.get("CFBundleIdentifier") not in ("com.openai.codex", "com.openai.chat"):
                return None
            if info.get("CFBundleIdentifier") == "com.openai.chat" and not (bundle / "Contents/Resources/codex").is_file():
                return None
        except (OSError, ValueError):
            return None
        return {"exe": str(path), "bundle": str(bundle)}
    if platform == "win32":
        if path.name.lower() not in ("chatgpt.exe", "codex.exe") or any(str(v).startswith("--type=") for v in args):
            return None
        # The CLI can also be named codex.exe. An Electron application resource
        # must exist beside the executable before treating it as the desktop app.
        if not (path.parent / "resources/app.asar").is_file():
            return None
        return {"exe": str(path)}
    return None


def windows_aumid(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetApplicationUserModelId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.UINT()
        if kernel.GetApplicationUserModelId(handle, ctypes.byref(size), None) != 122:
            return ""
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if kernel.GetApplicationUserModelId(handle, ctypes.byref(size), buffer) == 0 else ""
    finally:
        kernel.CloseHandle(handle)


def running_clients():
    clients = []
    for process in psutil.process_iter(["exe", "cmdline"]):
        try:
            target = desktop_target(process.info["exe"] or "", process.info["cmdline"] or [])
            if target:
                target.update(identity(process))
                if sys.platform == "win32":
                    target["aumid"] = windows_aumid(process.pid)
                clients.append(target)
        except (psutil.Error, OSError):
            continue
    return clients


def route_dependents():
    """Conservatively retain forwarding for desktop clients and existing CLIs."""
    values = []
    for process in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            name = (process.info["name"] or "").lower()
            if name in ("codex", "codex.exe", "chatgpt", "chatgpt.exe"):
                # The application's own quota reader never issues model requests.
                if process.ppid() == os.getppid() and "app-server" in (process.info["cmdline"] or []):
                    continue
                values.append(identity(process))
        except psutil.Error:
            continue
    return values


def _close_client(client):
    if sys.platform == "darwin":
        # NSRunningApplication.terminate is a normal quit request, without sending
        # AppleEvents to the app or changing macOS automation permissions.
        script = "ObjC.import('AppKit'); $.NSRunningApplication.runningApplicationWithProcessIdentifier(%d).terminate;" % client["pid"]
        subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, env=external_environment())
    else:
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        @callback
        def close(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == client["pid"]:
                user.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return True
        user.EnumWindows(close, 0)


def restart_running():
    clients = running_clients()
    if not clients:
        return False
    for client in clients:
        if not alive(client):
            continue
        process = psutil.Process(client["pid"])
        # Remember the bundled model backend: the GUI can leave it alive briefly
        # during shutdown. Never terminate shell commands started by a model.
        children = []
        root = Path(client.get("bundle") or client["exe"]).parent if "bundle" not in client else Path(client["bundle"])
        for child in process.children(recursive=True):
            try:
                if Path(child.exe()).is_relative_to(root):
                    children.append(identity(child))
            except psutil.Error:
                pass
        _close_client(client)
        deadline = time.monotonic() + 8
        while alive(client) and time.monotonic() < deadline:
            time.sleep(.1)
        for value in [client, *children]:
            if alive(value):
                try:
                    target = psutil.Process(value["pid"])
                    target.terminate()
                    try:
                        target.wait(3)
                    except psutil.TimeoutExpired:
                        if alive(value):
                            target.kill()
                            target.wait(3)
                except psutil.NoSuchProcess:
                    pass
        if alive(client):
            raise UpstreamError("Codex 客户端未能退出，已保留可用转发，请稍后重试")
    launched = set()
    for client in clients:
        if client["exe"] in launched:
            continue
        launched.add(client["exe"])
        if sys.platform == "darwin":
            result = subprocess.run(["/usr/bin/open", "-a", client["bundle"]],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, env=external_environment())
            if result.returncode:
                raise UpstreamError("未能重新打开 Codex 客户端；配置已恢复，可手动打开客户端")
        elif client.get("aumid"):
            subprocess.Popen([str(Path(os.environ.get("WINDIR", "C:/Windows")) / "explorer.exe"),
                              "shell:AppsFolder\\" + client["aumid"]], env=external_environment(), close_fds=True)
        else:
            subprocess.Popen([client["exe"]], cwd=str(Path(client["exe"]).parent), close_fds=True, env=external_environment())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if any(item["exe"] in launched for item in running_clients()):
            return True
        time.sleep(.3)
    raise UpstreamError("Codex 客户端启动未确认；已恢复当前路由，请手动打开客户端")
