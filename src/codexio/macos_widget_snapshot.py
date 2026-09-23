"""Compact, atomic data exchange with the sandboxed macOS WidgetKit extension."""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from codexio.logging_setup import get_logger
from codexio.model_display import display_effort, display_model

_bridge = None


def snapshot_path() -> Path:
    override = os.environ.get("CODEXIO_DATA_DIR")
    root = Path(override).expanduser() if override else Path.home() / "Library/Application Support/Codexio"
    return root / "widget_snapshot.json"


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _epoch(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _remaining(state, key):
    if state is None or getattr(getattr(state, "status", None), "value", None) != "ok":
        return None
    return _number(getattr(getattr(state, key, None), "remaining_percent", None))


def _has_window(state, key):
    return getattr(getattr(state, key, None), "remaining_percent", None) is not None


def _reset_at(state, key):
    if state is None or getattr(getattr(state, "status", None), "value", None) != "ok":
        return None
    return _epoch(getattr(getattr(state, key, None), "resets_at", None))


def make_snapshot(usage: dict | None, quota_state, *, now: float | None = None) -> dict:
    selected = (usage or {}).get("widget_request")
    summary = (usage or {}).get("menu_bar_today")
    if not isinstance(summary, dict):
        summaries = (usage or {}).get("summaries")
        summary = summaries.get("today") if isinstance(summaries, dict) else None
    summary = summary if isinstance(summary, dict) else {}
    request = None
    if isinstance(selected, dict) and selected.get("record_kind") == "user_request" and not selected.get("is_subagent"):
        prompt = str(selected.get("prompt_preview") or "").strip()
        if prompt.startswith("<send_user_message_question_reply>"):
            prompt = ""
        started = _epoch(selected.get("duration_started_at"))
        base = _number(selected.get("duration_base_ms")) or 0
        if started is not None and selected.get("duration_running") is True:
            started -= base / 1000
        request = {
            "id": str(selected.get("id") or "")[:180],
            "prompt": prompt[:240],
            "model": display_model(selected.get("model"))[:96],
            "reasoning_effort": display_effort(selected.get("reasoning_effort")),
            "cost_usd": _number(selected.get("cost_usd")),
            "duration_ms": _number(selected.get("duration_ms")),
            "duration_started_at": started,
            "duration_running": selected.get("duration_running") is True,
            "input_tokens": _count(selected.get("input_tokens")),
            "output_tokens": _count(selected.get("output_tokens")),
            "cached_input_tokens": _count(selected.get("cached_input_tokens")),
            "cache_hit_rate": _number(selected.get("cache_hit_rate")),
        }
    return {
        "schema": 1,
        "updated_at": time.time() if now is None else now,
        "request": request,
        "today": {
            "cost_usd": _number(summary.get("usd")),
            "tokens": _count(summary.get("tokens")) if summary else None,
        },
        "quota": {"five_hour": _remaining(quota_state, "five_hour"), "week": _remaining(quota_state, "week"),
                  "has_five_hour": _has_window(quota_state, "five_hour"), "has_week": _has_window(quota_state, "week"),
                  "week_reset_at": _reset_at(quota_state, "week")},
    }


def write_snapshot(snapshot: dict) -> str:
    """Return a content signature so callers can avoid unnecessary WidgetKit reloads."""
    path = snapshot_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    canonical = json.dumps({key: value for key, value in snapshot.items() if key != "updated_at"},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > 32_768:
        raise ValueError("小组件快照超过大小限制")
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reload_widget() -> bool:
    global _bridge
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return False
    try:
        if _bridge is None:
            app = Path(sys.executable).resolve().parents[2]
            _bridge = ctypes.CDLL(str(app / "Contents/Frameworks/libCodexioWidgetBridge.dylib"))
        _bridge.codexio_widget_reload()
        return True
    except (OSError, AttributeError):
        get_logger("widget").exception("请求系统刷新小组件失败")
        return False
