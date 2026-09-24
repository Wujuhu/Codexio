"""Blocking lifecycle operations, called by the GUI's background coordinator."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

import psutil

from codexio.upstream_client import alive, identity, restart_running, running_clients
from codexio.process_env import external_environment
from codexio.upstream_config import OfficialRoute, UpstreamError, codex_config_path, private_json, read_json


def verify_official_login():
    from codexio.codex_discovery import find_codex
    executable = find_codex()
    if executable is None:
        raise UpstreamError("未找到 Codex 客户端，未修改路由")
    try:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run([str(executable), "login", "status"], capture_output=True, timeout=15, env=external_environment(), **options)
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
            # A forwarding helper must never appear as another WidgetKit host.
            helper_widget = temporary / "Contents/PlugIns/CodexioWidget.appex"
            if helper_widget.exists():
                shutil.rmtree(helper_widget)
                subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", temporary],
                               capture_output=True, check=True)
            temporary.replace(target)
    else:
        cache.mkdir(parents=True, exist_ok=True)
        binary = cache / "Codexio.exe"
        if not binary.is_file():
            temporary = cache / "copy.exe"
            shutil.copy2(executable, temporary)
            temporary.replace(binary)
    _prune_runtime(directory / "runtime", cache)
    return [str(binary), "--upstream-proxy", str(directory)]


def _prune_runtime(root: Path, current: Path) -> None:
    """Retain recent and in-use helper bundles; remove only known old copies."""
    try:
        session = read_json(root.parent / "session.json")
        helper = session.get("process")
        if alive(helper):
            try:
                protected = Path(psutil.Process(helper["pid"]).exe()).resolve()
            except (OSError, psutil.Error):
                return
        else:
            protected = None
        candidates = sorted((path for path in root.iterdir() if path.is_dir() and not path.is_symlink()
                             and re.fullmatch(r"[0-9a-f]{16}", path.name)),
                            key=lambda path: path.stat().st_mtime_ns, reverse=True)
        running = {protected} if protected is not None else set()
        for process in psutil.process_iter(["exe", "name"]):
            executable = process.info.get("exe")
            if executable:
                running.add(Path(executable).resolve())
            elif str(process.info.get("name") or "").lower().startswith("codexio"):
                return
        keep = {current.resolve(), *(path.resolve() for path in candidates[:2])}
        for path in candidates:
            resolved = path.resolve()
            if resolved in keep or any(executable.is_relative_to(resolved) for executable in running):
                continue
            if time.time() - path.stat().st_mtime < 3600:
                continue
            if {item.name for item in path.iterdir()} - {"Codexio.app", "Codexio.exe"}:
                continue
            shutil.rmtree(path)
    except (OSError, psutil.Error):
        # Cleanup must never prevent the forwarding helper from starting.
        pass


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
        saved = read_json(self.directory / "client-routes.json").get("clients", [])
        self._loaded_routes = {(item["pid"], item["created"]): item["route"] for item in saved
                               if isinstance(item, dict) and all(key in item for key in ("pid", "created", "route"))}
        self._startup_route = self._current_route()
        self._route_history = [(0, self._startup_route)]

    def _current_route(self):
        if self.route.journal.exists():
            record = read_json(self.route.journal)
            applied = self.route._parse(record.get("applied", ""))
            return str(applied.get("openai_base_url") or applied.get("model_providers", {}).get("codexio-upstream", {}).get("base_url") or "direct")
        return "direct"

    def _remember_clients(self, route):
        clients = running_clients()
        for client in clients:
            loaded = next((value for stamp, value in reversed(self._route_history) if stamp <= client["created"]), route)
            self._loaded_routes.setdefault((client["pid"], client["created"]), loaded)
        keys = {(client["pid"], client["created"]) for client in clients}
        self._loaded_routes = {key: value for key, value in self._loaded_routes.items() if key in keys}
        private_json(self.directory / "client-routes.json", {"clients": [dict(pid=key[0], created=key[1], route=value)
                     for key, value in self._loaded_routes.items()]})
        return clients

    def clients_running(self):
        return bool(self._remember_clients(self._current_route()))

    def restart_needed(self):
        route = self._current_route()
        return any(self._loaded_routes[(client["pid"], client["created"])] != route
                   for client in self._remember_clients(route))

    def restart_client(self):
        restarted = self.restart()
        route = self._current_route()
        for client in running_clients():
            self._loaded_routes[(client["pid"], client["created"])] = route
        self._remember_clients(route)
        return restarted

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
        if previous.get("process") and alive(previous["process"]) and previous.get("protocol_version", 1) < 2:
            owner = previous.get("owner")
            if owner and owner != self.owner and alive(owner):
                raise UpstreamError("另一个 Codexio 正在使用上游检测")
            self.control("deactivate")
            self._route_history.append((time.time(), self._current_route()))
            self._stop_helper()
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
        self._route_history.append((time.time(), self._current_route()))
        return self.active

    def recover(self, *, update=False):
        self._remember_clients(self._startup_route)
        self.active = False
        previous = read_json(self.directory / "session.json")
        if self.route.journal.exists() or alive(previous.get("process")):
            return self.ensure(update=update)
        return False

    def enable(self):
        self._remember_clients(self._current_route())
        self.verify_login()
        self.ensure()
        self._remember_clients(self._current_route())
        self.control("activate")
        self.active = True
        self._route_history.append((time.time(), self._current_route()))
        return self.restart_needed()

    def disable(self):
        self._remember_clients(self._current_route())
        self.state = self.state or read_json(self.directory / "session.json")
        if alive(self.state.get("process")):
            self.control("deactivate")
        else:
            self.route.restore()
        self.active = False
        self._route_history.append((time.time(), "direct"))
        self._stop_helper()
        return self.restart_needed()

    def _stop_helper(self):
        process = self.state.get("process")
        if not alive(process):
            return
        if self.state.get("protocol_version", 1) >= 2:
            self.control("shutdown")
            deadline = time.monotonic() + 5
            while alive(process) and time.monotonic() < deadline:
                time.sleep(.05)
        if alive(process):
            # Only the private, identity-checked relay is stopped. Never a client.
            target = psutil.Process(process["pid"])
            if alive(process):
                target.terminate()
                try:
                    target.wait(3)
                except psutil.TimeoutExpired:
                    raise UpstreamError("本地代理尚未退出，请稍后重试") from None

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
            self.disable()
        except UpstreamError:
            self.route.restore()

    def healthy(self):
        return bool(self.control("health")["capture"])
