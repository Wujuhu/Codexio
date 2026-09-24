"""Compare observed desktop backend configuration with the current route.

Disk configuration is not a query of another process's memory. A baseline is
known only for a backend observed starting while its routing config was stable;
older processes without a saved baseline stay unknown until they restart.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time

import psutil
import tomlkit

from codexio.upstream_client import identity, running_clients
from codexio.upstream_config import UpstreamError, private_json, read_json
from codexio.logging_setup import get_logger


ROUTE_KEYS = {"model_provider", "model_providers", "openai_base_url", "chatgpt_base_url",
              "forced_login_method", "experimental_realtime_ws_base_url"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _merge(base, values):
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def client_contexts():
    """Only inspect the desktop's bundled backends, never our quota readers."""
    result = []
    for client in running_clients():
        backends = []
        try:
            root = Path(client.get("bundle") or Path(client["exe"]).parent).resolve()
            for process in psutil.Process(client["pid"]).children(recursive=True):
                try:
                    if "app-server" in process.cmdline() and Path(process.exe()).resolve().is_relative_to(root):
                        backends.append(process)
                except psutil.Error:
                    continue
        except psutil.Error:
            pass
        if not backends:
            result.append(dict(client=client, process=client, config=None))
        for process in backends:
            try:
                context = dict(client=client, process=identity(process), config=None)
            except psutil.Error:
                continue
            try:
                env, args = process.environ(), process.cmdline()
                home = Path(env.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
                if not home.is_absolute():
                    home = Path(process.cwd()) / home
                context.update(config=str((home / "config.toml").resolve()), overrides={}, profile=None,
                               environment={key: env[key] for key in ("OPENAI_BASE_URL", "CHATGPT_BASE_URL") if key in env})
                index = 0
                while index < len(args):
                    arg = args[index]
                    if arg in ("-c", "--config", "-p", "--profile") and index + 1 < len(args):
                        index += 1
                        option, value = arg, args[index]
                    elif arg.startswith(("--config=", "--profile=")):
                        option, value = arg.split("=", 1)
                    else:
                        index += 1
                        continue
                    if option in ("-p", "--profile"):
                        context["profile"] = value
                    elif value.split("=", 1)[0].strip().split(".")[0] in ROUTE_KEYS | {"profile"}:
                        _merge(context["overrides"], tomlkit.parse(value).unwrap())
                    index += 1
            except (psutil.Error, OSError, ValueError, tomlkit.exceptions.ParseError):
                context["config"] = None
            result.append(context)
    return result


def desktop_config_path(fallback):
    contexts = client_contexts()
    if any(not item["config"] for item in contexts):
        raise UpstreamError("无法确认当前桌面客户端的配置路径，请等待客户端启动完成后再开启上游检测")
    if any(item.get("environment") or ROUTE_KEYS.intersection(item.get("overrides", {})) for item in contexts):
        raise UpstreamError("客户端启动参数或环境变量覆盖了模型路由，未进行接管")
    paths = {item["config"] for item in contexts}
    if len(paths) > 1:
        raise UpstreamError("多个桌面客户端使用不同的 config.toml，请保留一个配置后再开启上游检测")
    return Path(next(iter(paths))) if paths else Path(fallback)


def _snapshot(context):
    path = Path(context["config"])
    modified = 0.0

    def read(source):
        nonlocal modified
        if not source.exists():
            return {}
        before = source.stat()
        parsed = tomlkit.parse(source.read_text(encoding="utf-8")).unwrap()
        after = source.stat()
        if (before.st_mtime_ns, before.st_ino, before.st_size) != (after.st_mtime_ns, after.st_ino, after.st_size):
            raise OSError("configuration changed during read")
        modified = max(modified, after.st_mtime)
        return parsed

    document = read(path)
    profile = context.get("profile") or context.get("overrides", {}).get("profile") or document.get("profile")
    if profile:
        profile_path = path.parent / (str(profile) + ".config.toml")
        _merge(document, read(profile_path) if profile_path.is_file() else document.get("profiles", {}).get(str(profile), {}))
    _merge(document, context.get("overrides", {}))
    provider = document.get("model_provider", "openai")
    route = {key: document[key] for key in ROUTE_KEYS - {"model_providers", "model_provider"} if key in document}
    route.update(model_provider=provider, provider=document.get("model_providers", {}).get(provider, {}),
                 environment=context.get("environment", {}))
    return _digest(route), modified


class RestartTracker:
    def __init__(self, directory):
        self.path = Path(directory) / "client-routes.json"
        saved = read_json(self.path)
        self.records = saved.get("clients", {}) if saved.get("version") == 2 else {}
        if not isinstance(self.records, dict):
            self.records = {}
        self.notified = [item for item in saved.get("notified", []) if isinstance(item, str)][-64:] if saved.get("version") == 2 and isinstance(saved.get("notified"), list) else []
        self.observations = {}
        self._saved = None
        self._lock = threading.RLock()

    def _save(self):
        value = dict(version=2, clients=self.records, notified=self.notified)
        signature = _digest(value)
        if signature != self._saved:
            try:
                private_json(self.path, value)
            except OSError:
                get_logger("upstream").warning("无法保存客户端配置基线，本次会话继续使用内存记录")
            self._saved = signature

    def assess(self):
        with self._lock:
            return self._assess()

    def _assess(self):
        pending, records, observations = [], {}, {}
        for context in client_contexts():
            process = context["process"]
            key = "%s:%.6f" % (process["pid"], process["created"])
            target = None
            context_key = _digest({name: context.get(name) for name in ("config", "overrides", "profile", "environment")})
            loaded = self.records.get(key, {})
            if not isinstance(loaded, dict):
                loaded = {}
            baseline = loaded.get("loaded") if loaded.get("context") == context_key else None
            try:
                if context["config"]:
                    target, modified = _snapshot(context)
                    previous = self.observations.get(context_key)
                    # Birth must follow a prior observation, with no subsequent
                    # file edit. Never assign today's disk values to an old PID.
                    if baseline is None and previous and previous[1] == target and previous[0] <= process["created"] and modified <= process["created"]:
                        baseline = target
                    observations[context_key] = (time.time(), target)
            except (OSError, ValueError, TypeError, AttributeError, tomlkit.exceptions.ParseError):
                pass
            records[key] = dict(context=context_key, loaded=baseline)
            if not target or baseline != target:
                pending.append(dict(process=key, target=target, context=context_key,
                                    status="changed" if target and baseline else "unknown"))
        self.records = records
        # Keep the last observation across a short interval with no client so a
        # manual restart can be recognized; only hashes, never config text.
        self.observations.update(observations)
        self._save()
        status = "changed" if any(row["status"] == "changed" for row in pending) else "unknown" if pending else "matched"
        token = _digest(sorted(pending, key=lambda row: row["process"]))
        return dict(status=status, token=token, notified=token in self.notified)

    def mark_notified(self, assessment):
        with self._lock:
            if assessment["token"] not in self.notified:
                self.notified = [*self.notified, assessment["token"]][-64:]
                self._save()
