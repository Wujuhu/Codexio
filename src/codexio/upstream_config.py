"""Transactional, credential-safe ownership of the active Codex provider route."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

import tomlkit

from codexio.provider_config import (
    BUILTIN_OPENAI,
    UNSUPPORTED_BUILTINS,
    ProviderConfigError,
    codex_config_path,
    delete_nested,
    get_nested,
    resolve_provider_config,
    set_nested,
)

ROUTE_ID = "codexio-upstream"  # Legacy v2 alias, removed during recovery only.
ROUTE_HEADER = "X-Codexio-Route"
OFFICIAL_ORIGIN = "https://chatgpt.com/backend-api/codex"
OPENAI_API_ORIGIN = "https://api.openai.com/v1"
ROUTE_VERSION = 3


class UpstreamError(Exception):
    pass


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
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
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
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _parse(text):
    try:
        return tomlkit.parse(text)
    except Exception as exc:
        raise UpstreamError("Codex 配置不是有效 TOML，已保留原文件") from exc


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        raise UpstreamError("无法读取 Codex 配置，未更改当前路由") from exc


def _safe_origin(value: str) -> str:
    try:
        parsed = urlsplit(str(value).strip())
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise UpstreamError("当前模型服务的 base_url 无效") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise UpstreamError("base_url 不得包含账号、密码、查询参数或 fragment；请使用 provider 的认证字段或 query_params")
    if port is not None and not 1 <= port <= 65535:
        raise UpstreamError("当前模型服务的 base_url 端口无效")
    return str(value).strip().rstrip("/")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


class ManagedRoute:
    def __init__(self, config: Path, directory: Path):
        self.config = Path(config).resolve()
        self.directory = Path(directory)
        self.journal = self.directory / "route.json"
        self.lock = self.directory / "route.lock"

    def plan(self, *, profile=None, auth_mode=None):
        try:
            provider = resolve_provider_config(self.config, profile=profile)
        except ProviderConfigError as exc:
            raise UpstreamError(str(exc)) from exc
        if provider.provider_id in UNSUPPORTED_BUILTINS:
            raise UpstreamError("当前内置模型供应商没有可安全接管的 Responses base_url")
        if provider.wire_api != "responses":
            raise UpstreamError("上游检测仅支持 Responses API 模型供应商")
        if provider.provider_id == BUILTIN_OPENAI:
            if provider.base_url:
                origin = provider.base_url
            elif provider.forced_login_method == "api" or str(auth_mode or "").lower() in ("api", "apikey"):
                origin = OPENAI_API_ORIGIN
            else:
                origin = OFFICIAL_ORIGIN
        else:
            origin = provider.base_url
        origin = _safe_origin(origin)
        session = read_json(self.directory / "session.json")
        own = (str(session.get("url") or "").rstrip("/") + "/" +
               str(session.get("route_token") or "") + "/v1")
        if session.get("url") and session.get("route_token") and origin == own:
            raise UpstreamError("当前模型供应商已指向本机路由，未重复接管")
        return {
            "root_config": str(provider.root_path),
            "provider_id": provider.provider_id,
            "profile": provider.profile,
            "origin": origin,
            "requires_openai_auth": provider.requires_openai_auth,
            "forced_login_method": provider.forced_login_method,
            "patch_path": str(provider.patch_path),
            "locator": list(provider.locator),
            "auth_mode": str(auth_mode or ""),
        }

    def install(self, base_url, route_token, *, profile=None, auth_mode=None, expected_origin=None):
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.path != "/v1":
            raise UpstreamError("上游检测只能使用本机回环代理")
        routed_url = base_url[:-3] + "/" + route_token + "/v1"
        with exclusive_lock(self.lock):
            if self.journal.exists():
                raise UpstreamError("上次路由尚未恢复，请先完成恢复")
            plan = self.plan(profile=profile, auth_mode=auth_mode)
            if expected_origin and plan["origin"] != expected_origin:
                raise UpstreamError("模型供应商配置已变化，未进行接管")
            patch_path = Path(plan["patch_path"])
            original_text = _read(patch_path)
            doc = _parse(original_text)
            locator = tuple(plan["locator"])
            original_present, original_endpoint = get_nested(doc, locator)
            if original_present and not isinstance(original_endpoint, str):
                raise UpstreamError("当前模型供应商的 base_url 字段无效")
            set_nested(doc, locator, routed_url)
            applied_text = tomlkit.dumps(doc)
            existed = patch_path.exists()
            mode = patch_path.stat().st_mode & 0o777 if existed else 0o600
            record = {
                "route_version": ROUTE_VERSION,
                "root_config": str(self.config),
                "config": str(patch_path),
                "profile": plan.get("profile"),
                "provider_id": plan["provider_id"],
                "locator": list(locator),
                "origin": plan["origin"],
                "original_present": original_present,
                "original_endpoint": original_endpoint if original_present else None,
                "applied_endpoint": routed_url,
                "original_digest": _digest([original_present, original_endpoint]),
                "applied_digest": _digest([True, routed_url]),
                "mode": mode,
                "existed": existed,
            }
            private_json(self.journal, record)
            if _read(patch_path) != original_text:
                self.journal.unlink(missing_ok=True)
                raise UpstreamError("Codex 配置同时发生变化，未进行接管，请重试")
            atomic_text(patch_path, applied_text, mode)
            return plan

    def _restore_v2(self, record):
        config = Path(record.get("config") or self.config).resolve()
        if not isinstance(record.get("original"), str) or not isinstance(record.get("applied"), str):
            raise UpstreamError("上游路由恢复记录无效，已保留转发")
        current = _read(config)
        if current == record["applied"]:
            if not record.get("existed", True):
                config.unlink(missing_ok=True)
            else:
                atomic_text(config, record["original"], int(record.get("mode", 0o600)))
            return
        doc = _parse(current)
        before = _parse(record["original"])
        applied = _parse(record["applied"])
        providers = doc.get("model_providers", {})
        applied_providers = applied.get("model_providers", {})
        owned = applied_providers.get(ROUTE_ID)
        actual = providers.get(ROUTE_ID) if hasattr(providers, "get") else None
        for key in ("openai_base_url", "model_provider"):
            if doc.get(key) == applied.get(key):
                if key in before:
                    doc[key] = before[key]
                else:
                    doc.pop(key, None)
        if actual is not None and owned is not None and actual.unwrap() == owned.unwrap():
            del providers[ROUTE_ID]
            if not providers and "model_providers" not in before:
                doc.pop("model_providers", None)
        elif actual is not None:
            raise UpstreamError("上游路由被外部修改，已保留转发；请重试恢复")
        restored = tomlkit.dumps(doc)
        if _read(config) != current:
            raise UpstreamError("Codex 配置同时发生变化，已保留转发，请重试恢复")
        if restored != current:
            atomic_text(config, restored, int(record.get("mode", 0o600)))

    def restore(self):
        with exclusive_lock(self.lock):
            if not self.journal.exists():
                return
            record = read_json(self.journal)
            if record.get("route_version") != ROUTE_VERSION:
                self._restore_v2(record)
                self.journal.unlink(missing_ok=True)
                return
            try:
                patch_path = Path(record["config"]).resolve()
                locator = tuple(str(item) for item in record["locator"])
                applied_endpoint = record["applied_endpoint"]
            except (KeyError, TypeError, ValueError):
                raise UpstreamError("上游路由恢复记录无效，已保留转发") from None
            current_text = _read(patch_path)
            doc = _parse(current_text)
            present, value = get_nested(doc, locator)
            original_present = record.get("original_present") is True
            original_endpoint = record.get("original_endpoint")
            if (record.get("original_digest") != _digest([original_present, original_endpoint])
                    or record.get("applied_digest") != _digest([True, applied_endpoint])):
                raise UpstreamError("上游路由恢复记录校验失败，已保留转发")
            if present and value == applied_endpoint:
                if original_present:
                    set_nested(doc, locator, original_endpoint)
                else:
                    delete_nested(doc, locator)
            elif present == original_present and value == original_endpoint:
                self.journal.unlink(missing_ok=True)
                return
            else:
                raise UpstreamError("上游路由被外部修改，已保留转发；请重试恢复")
            restored = tomlkit.dumps(doc)
            if _read(patch_path) != current_text:
                raise UpstreamError("Codex 配置同时发生变化，已保留转发，请重试恢复")
            if not record.get("existed", True) and not restored.strip():
                patch_path.unlink(missing_ok=True)
            elif restored != current_text:
                atomic_text(patch_path, restored, int(record.get("mode", 0o600)))
            self.journal.unlink(missing_ok=True)


# Keep imports from older modules working while the persisted v2 route is retired.
OfficialRoute = ManagedRoute
