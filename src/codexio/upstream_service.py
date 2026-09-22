"""Blocking lifecycle operations, called by the GUI's background coordinator."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

import psutil

from codexio.upstream_client import alive, identity, restart_running
from codexio.upstream_config import OfficialRoute, UpstreamError, codex_config_path, private_json, read_json


def verify_official_login():
    from codexio.codex_discovery import find_codex
    executable = find_codex()
    if executable is None:
        raise UpstreamError("未找到 Codex 客户端，未修改路由")
    try:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run([str(executable), "login", "status"], capture_output=True, timeout=15, **options)
    except (OSError, subprocess.TimeoutExpired):
        raise UpstreamError("无法确认官方登录状态，未修改路由") from None
    # Status output is examined in memory only; never persist credentials/output.
    status = (result.stdout + result.stderr).lower()
    if result.returncode or b"chatgpt" not in status or b"logged in" not in status:
        raise UpstreamError("请先使用 ChatGPT 官方账号登录 Codex，再开启上游检测")


def helper_command(directory):
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-m", "codexio", "--upstream-proxy", str(directory)]
    executable = Path(sys.executable).resolve()
    stamp = hashlib.sha256((str(executable) + str(executable.stat().st_mtime_ns)).encode()).hexdigest()[:16]
    cache = directory / "runtime" / stamp
    # Keep the guardian outside the replaceable application bundle. The updater
    # may remove the old APP/EXE while this process is still forwarding requests.
    if sys.platform == "darwin":
        bundle = executable.parent.parent.parent
        target = cache / "Codexio.app"
        binary = target / "Contents/MacOS" / executable.name
        if not binary.is_file():
            temporary = cache / "copy.app"
            cache.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(temporary, ignore_errors=True)
            shutil.copytree(bundle, temporary, symlinks=True)
            temporary.replace(target)
    else:
        cache.mkdir(parents=True, exist_ok=True)
        binary = cache / "Codexio.exe"
        if not binary.is_file():
            temporary = cache / "copy.exe"
            shutil.copy2(executable, temporary)
            temporary.replace(binary)
    return [str(binary), "--upstream-proxy", str(directory)]


class UpstreamService:
    def __init__(self, data_directory, *, config_path=None, restart=restart_running, verify_login=verify_official_login):
        self.directory = Path(data_directory) / "upstream"
        self.route = OfficialRoute(config_path or codex_config_path(), self.directory)
        self.restart = restart
        self.verify_login = verify_login
        self.owner = identity(psutil.Process())
        self.state = {}
        self.active = False
        self.handed_off = False
        self.process = None

    def control(self, action, **data):
        state = self.state or read_json(self.directory / "session.json")
        if not state.get("url", "").startswith("http://127.0.0.1:"):
            raise UpstreamError("本地转发服务尚未就绪")
        request = Request(state["url"] + "/_codexio/" + action, json.dumps(data).encode(),
                          headers={"Authorization": "Bearer " + state["control_token"], "Content-Type": "application/json"})
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=8) as response:
                result = json.load(response)
        except HTTPError as exc:
            try:
                message = json.load(exc).get("error")
            except (ValueError, AttributeError):
                message = None
            raise UpstreamError(message or "本地转发服务拒绝操作，当前配置已保留") from None
        except (OSError, URLError, ValueError):
            raise UpstreamError("本地转发服务连接失败，正在保留恢复记录") from None
        if result.get("error"):
            raise UpstreamError(result["error"])
        return result

    def ensure(self, *, update=False):
        self.directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.directory.chmod(0o700)
        previous = read_json(self.directory / "session.json")
        self.state = previous
        if previous.get("process") and alive(previous["process"]):
            self.control("health")
        else:
            # Reuse the port/token when recovering an abandoned route so a client
            # with the old provider still in memory can continue using it.
            recovering = self.route.journal.exists()
            if recovering and not previous.get("route_token"):
                self.route.restore()
                raise UpstreamError("已恢复遗留配置；请重新开启上游检测以重启客户端")
            self.state = dict(config=str(self.route.config), owner=self.owner,
                              route_token=previous.get("route_token") if recovering else secrets.token_urlsafe(32),
                              control_token=secrets.token_urlsafe(32),
                              port=previous.get("port", 0) if recovering else 0)
            command = helper_command(self.directory)
            private_json(self.directory / "session.json", self.state)
            options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                       "stderr": subprocess.DEVNULL, "close_fds": True}
            env = dict(os.environ)
            env.pop("CODEXIO_UPDATE_JOB", None)
            if getattr(sys, "frozen", False):
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            elif not env.get("PYTHONPATH"):
                env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
            if os.name == "nt":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            else:
                options["start_new_session"] = True
            self.process = subprocess.Popen(command, env=env, **options)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                self.state = read_json(self.directory / "session.json")
                if self.state.get("process") and alive(self.state["process"]):
                    self.control("health")
                    break
                if self.process.poll() is not None:
                    self.route.restore()
                    raise UpstreamError("无法启动本地转发，已恢复原路由")
                time.sleep(.1)
            else:
                self.route.restore()
                raise UpstreamError("本地转发启动超时，已恢复原路由")
        result = self.control("claim", owner=self.owner, update=update)
        self.active = bool(result["capture"])
        return self.active

    def recover(self, *, update=False):
        self.active = False
        previous = read_json(self.directory / "session.json")
        if self.route.journal.exists() or alive(previous.get("process")):
            return self.ensure(update=update)
        return False

    def enable(self):
        self.verify_login()
        self.ensure()
        self.control("activate")
        self.active = True
        try:
            restarted = self.restart()
        except Exception as exc:
            self.control("deactivate")
            self.active = False
            raise UpstreamError(str(exc) if isinstance(exc, UpstreamError) else "客户端重启失败，已恢复直连配置") from None
        return "已开启 · 已自动重启 ChatGPT" if restarted else "已开启 · 下次打开 ChatGPT 生效"

    def disable(self, *, restart=True):
        if not alive(self.state.get("process")):
            self.ensure()
        self.control("deactivate")
        self.active = False
        restarted = self.restart() if restart else False
        return "已关闭 · 已自动重启 ChatGPT" if restarted else "已关闭 · 官方直连"

    def prepare_update(self):
        if self.active:
            self.control("handoff")
            self.handed_off = True

    @property
    def needs_restore(self):
        return self.active or self.route.journal.exists()

    def abandon(self):
        if self.handed_off or not self.state:
            return
        try:
            self.control("abandon")
        except UpstreamError:
            self.route.restore()

    def healthy(self):
        return bool(self.control("health")["capture"])
