"""Versioned preferences for the usage dashboard (no authentication credentials)."""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from aiquota.settings import data_dir


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_config() -> dict:
    return {
        "version": 1,
        "theme": "system",
        "widget_visible": True,
        "auto_sync_prices": True,
        "auto_update": True,
        "codex_roots": [os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")],
        "ssh_sources": [],
        "account_since": utc_now(),
        "history_assignments": [],
        "main_geometry": None,
    }


def load_analytics_config(path: Path | None = None) -> dict:
    target = path or data_dir() / "analytics_settings.json"
    config = default_config()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            config.update({k: v for k, v in raw.items() if k in config})
    except (OSError, ValueError):
        pass
    if config["theme"] not in ("system", "dark", "light"):
        config["theme"] = "system"
    for key in ("ssh_sources", "history_assignments"):
        if not isinstance(config[key], list):
            config[key] = []
        config[key] = [v for v in config[key] if isinstance(v, dict)]
    if not isinstance(config["codex_roots"], list):
        config["codex_roots"] = default_config()["codex_roots"]
    config["codex_roots"] = list(dict.fromkeys(str(v) for v in config["codex_roots"] if str(v).strip()))
    for key in ("widget_visible", "auto_sync_prices", "auto_update"):
        config[key] = config[key] if isinstance(config[key], bool) else False
    try:
        datetime.fromisoformat(str(config["account_since"]).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        config["account_since"] = utc_now()
    return copy.deepcopy(config)


def save_analytics_config(config: dict, path: Path | None = None) -> None:
    config = {key: value for key, value in config.items() if key not in ("lan_sources", "share_tokens")}
    target = path or data_dir() / "analytics_settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
