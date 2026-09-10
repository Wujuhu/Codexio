"""Read selector-visible model metadata from the local Codex model cache.

The Codex cache uses ``models[].slug`` and ``visibility: "list" | "hide"``.
The model/list representation (``model``/``id`` and explicit ``hidden``) is
also accepted inside that cache. ``supported_in_api`` is deliberately ignored:
a model such as Codex Spark can be selectable with a subscription without API
availability. No usage history, price catalog, credentials or network is read.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MAX_CACHE_BYTES = 32 * 1024 * 1024
_LOCK = threading.RLock()


@dataclass(frozen=True)
class _Snapshot:
    signature: tuple
    models: tuple[str, ...]
    updated_at: str


_LAST_VALID: dict[str, _Snapshot] = {}


def _timestamp(value, fallback: float) -> str:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback, timezone.utc).isoformat()


def _visible_models(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise ValueError("invalid model cache schema")
    entries = payload["models"]
    visible = []
    recognized = 0
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        name = entry.get("slug") or entry.get("model") or entry.get("id")
        if not isinstance(name, str) or not _MODEL_ID.fullmatch(name):
            continue
        hidden = entry.get("hidden")
        visibility = entry.get("visibility")
        # Missing or novel selector flags are not evidence that a model is
        # offered. Fail closed instead of leaking internal/unavailable models.
        if "hidden" in entry and not isinstance(hidden, bool):
            continue
        if visibility not in (None, "list", "hide"):
            continue
        if visibility is None and hidden is None:
            continue
        recognized += 1
        if hidden is True or visibility == "hide":
            continue
        priority = entry.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool):
            priority = 10**9
        visible.append((priority, position, name))
    if entries and not recognized:
        raise ValueError("no recognized selector metadata")
    return tuple(dict.fromkeys(name for _, _, name in sorted(visible)))


def _read_cache(path: Path, previous: Optional[_Snapshot]) -> _Snapshot:
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
    if previous is not None and signature == previous.signature:
        return previous
    if stat.st_size > _MAX_CACHE_BYTES:
        raise ValueError("model cache too large")
    with path.open("r", encoding="utf-8-sig") as stream:
        # The byte-size guard above and bounded read also cover a growing file.
        raw = stream.read(_MAX_CACHE_BYTES + 1)
    if len(raw) > _MAX_CACHE_BYTES:
        raise ValueError("model cache too large")
    payload = json.loads(raw)
    models = _visible_models(payload)
    return _Snapshot(signature, models, _timestamp(payload.get("fetched_at"), stat.st_mtime))


def load_available_models(roots: list[str]) -> dict:
    """Return the ordered union of visible models from configured local roots.

    ``status`` is ``ok`` for valid caches, ``stale`` when at least one root
    cannot be refreshed but a valid list remains, or ``unavailable`` when no
    root has ever yielded valid metadata. A malformed/deleted cache retains
    its last valid in-process snapshot. An explicitly empty valid cache clears
    that root's list. Removing a root also removes its models from the result.
    This function never falls back to all API models or previously used models.
    """
    snapshots = []
    failures = 0
    seen = set()
    with _LOCK:
        for root in roots:
            if not isinstance(root, str) or not root.strip():
                failures += 1
                continue
            try:
                path = Path(root).expanduser().resolve() / "models_cache.json"
                key = os.path.normcase(str(path))
            except (OSError, ValueError, RuntimeError):
                failures += 1
                continue
            if key in seen:
                continue
            seen.add(key)
            previous = _LAST_VALID.get(key)
            try:
                current = _read_cache(path, previous)
                _LAST_VALID[key] = current
                snapshots.append(current)
            except (OSError, ValueError, UnicodeError, RecursionError, OverflowError):
                failures += 1
                if previous is not None:
                    snapshots.append(previous)
    if not snapshots:
        return {"models": [], "status": "unavailable", "reason": "未读取到本机 Codex 可用模型列表"}
    result = {
        "models": list(dict.fromkeys(model for snapshot in snapshots for model in snapshot.models)),
        "status": "stale" if failures else "ok",
        "updated_at": min(snapshot.updated_at for snapshot in snapshots),
    }
    if failures:
        result["reason"] = "部分本机模型列表暂不可读取；保留最近有效列表"
    elif not result["models"]:
        result["reason"] = "本机 Codex 模型列表暂无可显示模型"
    return result
