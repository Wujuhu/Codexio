"""Resolve the effective global Codex model provider without reading credentials."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import tomlkit


BUILTIN_OPENAI = "openai"
UNSUPPORTED_BUILTINS = {"amazon-bedrock", "ollama", "lmstudio"}


class ProviderConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    root_path: Path
    patch_path: Path
    locator: tuple[str, ...]
    provider_id: str
    profile: str | None
    base_url: str | None
    forced_login_method: str | None
    requires_openai_auth: bool
    wire_api: str

    @property
    def custom(self) -> bool:
        return self.provider_id != BUILTIN_OPENAI


def codex_config_path() -> Path:
    return (Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser() / "config.toml").resolve()


def _read(path: Path):
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return text, tomlkit.parse(text)
    except OSError as exc:
        raise ProviderConfigError("无法读取 Codex 配置") from exc
    except Exception as exc:
        raise ProviderConfigError("Codex 配置不是有效 TOML") from exc


def _plain(value: Any):
    try:
        return value.unwrap()
    except AttributeError:
        return value


def _merge(base: dict, overlay: dict) -> dict:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _table(value) -> dict:
    value = _plain(value)
    return value if isinstance(value, dict) else {}


def resolve_provider_config(config_path: Path | str | None = None, *, profile: str | None = None) -> ProviderConfig:
    """Return the effective provider and the exact base-URL field to patch.

    Only the user's global Codex configuration is considered. Project config is
    intentionally irrelevant because Codex ignores routing keys there.
    """
    root_path = Path(config_path or codex_config_path()).expanduser().resolve()
    _root_text, root_doc = _read(root_path)
    root = _table(root_doc)
    selected = str(profile or root.get("profile") or "").strip() or None
    overlay = {}
    overlay_path = root_path
    overlay_prefix: tuple[str, ...] = ()
    if selected:
        separate = root_path.parent / (selected + ".config.toml")
        if separate.is_file():
            _profile_text, profile_doc = _read(separate)
            overlay = _table(profile_doc)
            overlay_path = separate.resolve()
        else:
            profiles = _table(root_doc.get("profiles", {}))
            if selected not in profiles:
                raise ProviderConfigError("当前配置方案不存在或无法读取")
            overlay = _table(profiles.get(selected, {}))
            overlay_prefix = ("profiles", selected)
    effective = _merge(root, overlay)
    provider_id = str(effective.get("model_provider") or BUILTIN_OPENAI).strip()
    if not provider_id:
        provider_id = BUILTIN_OPENAI
    forced = str(effective.get("forced_login_method") or "").strip().lower() or None

    def source_for_top(key: str) -> tuple[Path, tuple[str, ...]]:
        if key in overlay:
            return overlay_path, overlay_prefix + (key,)
        if key in root:
            return root_path, (key,)
        if selected:
            return overlay_path, overlay_prefix + (key,)
        return root_path, (key,)

    if provider_id == BUILTIN_OPENAI:
        patch_path, locator = source_for_top("openai_base_url")
        base_url = effective.get("openai_base_url")
        base_url = str(base_url).strip() if isinstance(base_url, str) and base_url.strip() else None
        return ProviderConfig(root_path, patch_path, locator, provider_id, selected, base_url,
                              forced, True, "responses")

    root_providers = _table(root_doc.get("model_providers", {}))
    overlay_providers = _table(overlay.get("model_providers", {}))
    base_provider = _table(root_providers.get(provider_id, {}))
    profile_provider = _table(overlay_providers.get(provider_id, {}))
    provider = _merge(base_provider, profile_provider)
    if not provider:
        raise ProviderConfigError("当前模型供应商未在 model_providers 中定义")
    if "base_url" in profile_provider:
        patch_path = overlay_path
        locator = overlay_prefix + ("model_providers", provider_id, "base_url")
    elif "base_url" in base_provider:
        patch_path = root_path
        locator = ("model_providers", provider_id, "base_url")
    else:
        raise ProviderConfigError("当前模型供应商没有可接管的 base_url")
    base_url = provider.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderConfigError("当前模型供应商的 base_url 无效")
    wire_api = str(provider.get("wire_api") or "responses").strip().lower()
    requires = provider.get("requires_openai_auth", False)
    if not isinstance(requires, bool):
        raise ProviderConfigError("当前模型供应商的 requires_openai_auth 无效")
    return ProviderConfig(root_path, patch_path, locator, provider_id, selected, base_url.strip(),
                          forced, requires, wire_api)


def get_nested(document, locator: tuple[str, ...]):
    current = document
    for key in locator:
        try:
            current = current[key]
        except (KeyError, TypeError):
            return False, None
    return True, _plain(current)


def set_nested(document, locator: tuple[str, ...], value) -> None:
    if not locator:
        raise ProviderConfigError("配置字段路径为空")
    current = document
    for key in locator[:-1]:
        if key not in current:
            current[key] = tomlkit.table()
        current = current[key]
        if not hasattr(current, "__setitem__"):
            raise ProviderConfigError("模型供应商配置路径无效")
    current[locator[-1]] = value


def delete_nested(document, locator: tuple[str, ...]) -> None:
    if not locator:
        return
    current = document
    for key in locator[:-1]:
        if key not in current or not hasattr(current[key], "__delitem__"):
            return
        current = current[key]
    current.pop(locator[-1], None)
