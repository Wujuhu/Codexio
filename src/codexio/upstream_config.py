"""Transactional, reversible ownership of a Codex official proxy route.

Only config.toml is managed here. Native OAuth credentials are never read or
written, and the recovery journal is private to the current OS user.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

import tomlkit

ROUTE_ID = "codexio-upstream"
ROUTE_HEADER = "X-Codexio-Route"
OFFICIAL_ORIGIN = "https://chatgpt.com/backend-api/codex"


class UpstreamError(Exception):
    pass


def codex_config_path():
    return (Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser() / "config.toml").resolve()


def atomic_text(path: Path, text: str, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def private_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise UpstreamError("另一个上游检测操作正在修改配置，请稍后重试")
                time.sleep(.05)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class OfficialRoute:
    def __init__(self, config: Path, directory: Path):
        self.config = config.resolve()
        self.directory = directory
        self.journal = directory / "route.json"
        self.lock = directory / "route.lock"

    def _read(self):
        try:
            return self.config.read_text(encoding="utf-8") if self.config.exists() else ""
        except OSError as exc:
            raise UpstreamError("无法读取 Codex 配置，未更改当前路由") from exc

    @staticmethod
    def _parse(text):
        try:
            return tomlkit.parse(text)
        except Exception as exc:
            raise UpstreamError("Codex 配置不是有效 TOML，已保留原文件") from exc

    def preflight(self):
        doc = self._parse(self._read())
        if doc.get("model_provider", "openai") != "openai":
            raise UpstreamError("上游检测仅接管官方直连；请先关闭其他工具的模型路由")
        if ROUTE_ID in doc.get("model_providers", {}):
            raise UpstreamError("检测到未归属的上游路由配置，未覆盖现有配置")
        if doc.get("forced_login_method") == "api" or doc.get("openai_base_url") or doc.get("chatgpt_base_url"):
            raise UpstreamError("当前配置不是默认 ChatGPT 官方路由，未进行接管")
        profile = doc.get("profile")
        if profile:
            override = doc.get("profiles", {}).get(str(profile), {})
            profile_file = self.config.parent / (str(profile) + ".config.toml")
            if profile_file.is_file():
                override = self._parse(profile_file.read_text(encoding="utf-8"))
            if any(key in override for key in ("model_provider", "model_providers", "openai_base_url", "chatgpt_base_url")):
                raise UpstreamError("当前配置方案覆盖了模型路由，未进行接管")

    def install(self, base_url, route_token):
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.path != "/v1":
            raise UpstreamError("上游检测只能使用本机回环代理")
        with exclusive_lock(self.lock):
            if self.journal.exists():
                raise UpstreamError("上次路由尚未恢复，请先完成恢复")
            self.preflight()
            original = self._read()
            doc = self._parse(original)
            routed_url = base_url[:-3] + "/" + route_token + "/v1"
            table = tomlkit.table()
            table.update(name="OpenAI", base_url=routed_url, wire_api="responses",
                         requires_openai_auth=True, supports_websockets=True)
            table["http_headers"] = {ROUTE_HEADER: route_token}
            # Keep the built-in provider identity, including its dynamic model
            # catalog and existing conversations. Retain the alias for old turns.
            doc["model_provider"] = "openai"
            doc["openai_base_url"] = routed_url
            if "model_providers" not in doc:
                doc["model_providers"] = tomlkit.table()
            doc["model_providers"][ROUTE_ID] = table
            applied = tomlkit.dumps(doc)
            mode = self.config.stat().st_mode & 0o777 if self.config.exists() else 0o600
            record = dict(config=str(self.config), original=original, applied=applied, mode=mode, route_version=2,
                          existed=self.config.exists(), original_provider=self._parse(original).get("model_provider"))
            private_json(self.journal, record)
            if self._read() != original:
                self.journal.unlink()
                raise UpstreamError("Codex 配置同时发生变化，未进行接管，请重试")
            atomic_text(self.config, applied, mode)

    def restore(self):
        with exclusive_lock(self.lock):
            if not self.journal.exists():
                return
            record = read_json(self.journal)
            if record.get("config") != str(self.config) or not isinstance(record.get("original"), str):
                raise UpstreamError("上游路由恢复记录无效，已保留转发")
            current = self._read()
            if current == record["applied"]:
                if not record["existed"]:
                    self.config.unlink(missing_ok=True)
                else:
                    atomic_text(self.config, record["original"], record["mode"])
            else:
                doc = self._parse(current)
                owned = self._parse(record["applied"])["model_providers"][ROUTE_ID].unwrap()
                providers = doc.get("model_providers", {})
                actual = providers.get(ROUTE_ID)
                if record.get("route_version") == 2:
                    before = self._parse(record["original"])
                    applied = self._parse(record["applied"])
                    for key in ("openai_base_url", "model_provider"):
                        if doc.get(key) == applied.get(key):
                            if key in before:
                                doc[key] = before[key]
                            else:
                                doc.pop(key, None)
                if doc.get("model_provider") == ROUTE_ID:
                    if actual is None or actual.unwrap() != owned:
                        raise UpstreamError("上游路由被外部修改，已保留转发；请重试恢复")
                    if record["original_provider"] is None:
                        del doc["model_provider"]
                    else:
                        doc["model_provider"] = record["original_provider"]
                if actual is not None and actual.unwrap() == owned:
                    del providers[ROUTE_ID]
                    if not providers and "model_providers" not in self._parse(record["original"]):
                        del doc["model_providers"]
                restored = tomlkit.dumps(doc)
                if self._read() != current:
                    raise UpstreamError("Codex 配置同时发生变化，已保留转发，请重试恢复")
                if restored != current:
                    atomic_text(self.config, restored, record["mode"])
            self.journal.unlink()
