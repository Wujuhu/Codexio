"""Incremental Codex JSONL accounting using only Python's standard library.

This module also runs over SSH without installing anything on the remote host.
Only metadata, bounded previews of visible user/assistant messages and token
counters leave the reader. Agent routing identities can link child requests;
tool bodies, reasoning and credentials are never indexed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PARSER_VERSION = 5
PREVIEW_LIMIT = 600
COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
            "output_tokens", "reasoning_output_tokens", "total_tokens")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(value) -> str:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def _duration_ms(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _call_timing(payload):
    # Only explicit model-call timing belongs on an accounting record. Tool
    # item timings and turn durations cannot stand in for an API call duration.
    duration = _duration_ms(payload.get("duration_ms"))
    start = _timestamp(payload.get("started_at"))
    end = _timestamp(payload.get("completed_at") or payload.get("ended_at"))
    if duration is None and start and end:
        span = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() * 1000
        duration = _duration_ms(span)
    result = {"duration_ms": duration} if duration is not None else {}
    if start:
        result["call_started_at"] = start
    if end:
        result["call_ended_at"] = end
    return result


def _hash(value) -> str:
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _plain(value, limit=400) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("text", "input_text"):
                raw = item.get("text", "")
                if isinstance(raw, str):
                    parts.append(raw)
        text = "\n".join(parts)
    else:
        return ""
    return " ".join("".join(ch for ch in text if ch.isprintable() or ch in "\r\n\t").split())[:limit]


def _user_preview(value) -> str:
    text = _plain(value, 12000)
    # Older rollouts sometimes persisted environment/AGENTS injections as user
    # messages. Prefer the actual request marker when it is present, otherwise
    # do not surface these generated wrappers as a person's prompt.
    for marker in ("## My request:", "## My request", "<user_request>"):
        if marker in text:
            return text.split(marker, 1)[1].strip()[:PREVIEW_LIMIT]
    wrappers = ("# AGENTS.md instructions", "<environment_context>", "<permissions", "<INSTRUCTIONS>",
                "<user_instructions>", "<developer_instructions>", "<system", "<app-context>")
    if text.lstrip().startswith(wrappers):
        return ""
    return text[:PREVIEW_LIMIT]


def _assistant_preview(payload) -> str:
    """Accept only visible message text, never reasoning or tool payloads."""
    if payload.get("type", "message") != "message" or payload.get("role") != "assistant":
        return ""
    if payload.get("phase") not in (None, "", "commentary", "final", "final_answer"):
        return ""
    if payload.get("channel") not in (None, "", "commentary", "final"):
        return ""
    if payload.get("recipient") not in (None, "", "all", "user"):
        return ""
    content = payload.get("content")
    if isinstance(content, list):
        content = "\n".join(item["text"] for item in content
                            if isinstance(item, dict) and item.get("type") in ("text", "output_text")
                            and isinstance(item.get("text"), str))
    return _plain(content, PREVIEW_LIMIT)


def _remember_preview_record(state, record):
    # A small response-ID lookup permits explicit, out-of-order metadata updates
    # across incremental scans without retaining whole conversations in cursors.
    response_id = record.get("response_id")
    if response_id:
        recent = state.setdefault("preview_recent", {})
        recent[response_id] = dict(record)
        while len(recent) > 16:
            recent.pop(next(iter(recent)))


def _take_output_preview(state, response_id, turn_id) -> str:
    pending = state.get("preview_pending", [])
    matched, remaining = [], []
    for item in pending:
        if (item.get("response_id") in (None, "", response_id)
                and (not item.get("turn_id") or item["turn_id"] == turn_id)):
            matched.append(item["text"])
        else:
            remaining.append(item)
    state["preview_pending"] = remaining
    return " ".join(dict.fromkeys(matched))[:PREVIEW_LIMIT]


def _capture_output(entry, payload, state):
    text = _assistant_preview(payload)
    if not text:
        return []
    metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    owner = payload.get("thread_id") or metadata.get("thread_id")
    if owner and str(owner) != state.get("session_id"):
        return []
    explicit_turn = str(payload.get("turn_id") or metadata.get("turn_id") or "")
    turn_id = explicit_turn or str(state.get("turn_id") or "")
    response_id = str(payload.get("response_id") or entry.get("response_id") or "")
    recent = state.get("preview_recent", {})
    if response_id and response_id in recent:
        old = recent[response_id]
        if old.get("context_owner_verified") and (not explicit_turn or explicit_turn == old.get("turn_id")):
            parts = [old.get("output_preview", ""), text]
            record = dict(old, output_preview=" ".join(dict.fromkeys(part for part in parts if part))[:PREVIEW_LIMIT])
            _remember_preview_record(state, record)
            candidate = state.get("modern_candidate")
            if candidate and candidate.get("response_id") == response_id:
                candidate["record"] = record
            return [record]
        return []
    # Without a response ID a visible message belongs to the next accounting
    # boundary. Never apply a turn's final answer to preceding tool requests.
    if turn_id and turn_id != state.get("turn_id"):
        return []
    pending = state.setdefault("preview_pending", [])
    key = str(payload.get("id") or _hash([response_id, turn_id, text]))
    if not any(item.get("key") == key for item in pending):
        pending.append(dict(key=key, response_id=response_id, turn_id=turn_id, text=text))
        del pending[:-16]
    return []


def _usage(raw) -> Any:
    if not isinstance(raw, dict) or not any(k in raw for k in COUNTERS):
        return None
    result = []
    for key in COUNTERS:
        value = raw.get(key, raw.get("cache_read_input_tokens", 0) if key == "cached_input_tokens" else 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 9223372036854775807:
            return None
        result.append(value)
    # total is useful as a diagnostic/signature but not as a second billable sum.
    return result


def _billable(raw, value) -> bool:
    return (value is not None and isinstance(raw, dict)
            and "input_tokens" in raw and "output_tokens" in raw
            and value[0] + value[3] > 0)


def _signature(total, last) -> str:
    return _hash([total, last])


def _stopped(stop) -> bool:
    if stop is None:
        return False
    return bool(stop.is_set()) if hasattr(stop, "is_set") else bool(stop())


def session_files(root: Path) -> list:
    """One physical rollout per UUID, even while an archive move is in flight."""
    found = {}
    for directory in (root / "sessions", root / "archived_sessions"):
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.jsonl"):
            try:
                # Do not follow file symlinks outside the explicitly chosen root.
                if path.is_symlink():
                    continue
                ids = UUID_RE.findall(path.stem)
                key = ids[-1].lower() if ids else path.name
                old = found.get(key)
                if old is None or path.stat().st_size > old.stat().st_size:
                    found[key] = path
            except OSError:
                continue
    return sorted(found.values(), key=lambda p: str(p))


def _rollout_id(path: Path) -> str:
    ids = UUID_RE.findall(path.stem)
    return ids[-1].lower() if ids else _hash(path.name)[:32]


def cursor_key(root: Path, path: Path, source_id: str) -> str:
    return "usage:{}:{}:{}".format(source_id, _hash(os.path.normcase(str(root.resolve())))[:16], _rollout_id(path))


def read_session_titles(root: Path) -> dict:
    def clean_title(value) -> str:
        if not isinstance(value, str):
            return ""
        title = _plain(value)
        if title.startswith(("# Files mentioned by the user:", "# AGENTS.md instructions")):
            return ""
        return title

    titles = {}
    index = root / "session_index.jsonl"
    try:
        with index.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                    ident = item.get("id") or item.get("thread_id")
                    title = next((title for field in ("thread_name", "title", "name")
                                  if (title := clean_title(item.get(field)))), "")
                    if ident and title:
                        titles[str(ident)] = title
                except (ValueError, AttributeError):
                    continue
    except OSError:
        pass
    # Restrict both table and columns; do not SELECT * from the Codex state DB.
    for path in sorted(root.glob("state*.sqlite"), reverse=True):
        db = None
        try:
            db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
            db.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in db.execute("PRAGMA table_info(threads)")}
            if {"id", "title"}.issubset(columns):
                for ident, title in db.execute("SELECT id,title FROM threads WHERE title IS NOT NULL"):
                    title = clean_title(title)
                    if title:
                        # The session index carries display names/renames. The
                        # database may still contain an initial prompt title.
                        titles.setdefault(str(ident), title)
                break
        except (sqlite3.Error, OSError):
            continue
        finally:
            if db is not None:
                db.close()
    return titles


def _initial_state(path: Path) -> dict:
    return {"rollout_id": _rollout_id(path), "session_id": _rollout_id(path),
            "turn_id": "", "model": "unknown", "service_tier": None,
            "provider": "unknown", "prompt_preview": "", "meta_seen": False,
            "last_by_source": {}, "previous_signature": None, "high_water": None,
            "last_totals": {}, "replay_done": False, "parent_position": 0,
            "modern_candidate": None, "active_response_id": None,
            "preview_pending": [], "preview_recent": {},
            "diagnostics": {}}


def _turn_key(session_id, turn_id):
    return "turn:{}:{}".format(session_id, turn_id)


def _turn_source(state, payload):
    source = payload.get("source")
    spawn = (((source.get("subagent") or {}).get("thread_spawn") or {})
             if isinstance(source, dict) and isinstance(source.get("subagent"), dict) else {})
    state["request_source"] = {
        "is_subagent": bool(spawn),
        "parent_session_id": str(spawn.get("parent_thread_id") or (payload.get("parent_thread_id") if spawn else "") or ""),
        "parent_turn_id": str(spawn.get("parent_turn_id") or payload.get("parent_turn_id") or ""),
        "agent_path": str(spawn.get("agent_path") or ""),
    }


def _turn_inherited(entry, state):
    boundary, ordinal = state.get("history_start"), entry.get("ordinal")
    if isinstance(boundary, int) and isinstance(ordinal, int):
        return ordinal < boundary
    stamp = _timestamp(entry.get("timestamp"))
    return bool(state.get("parent_id") and stamp and state.get("fork_timestamp") and stamp < state["fork_timestamp"])


def _turn_owned(entry, state):
    if not state.get("parent_id"):
        return True
    boundary, ordinal = state.get("history_start"), entry.get("ordinal")
    return isinstance(boundary, int) and isinstance(ordinal, int) and ordinal >= boundary


def _turn_get(state, turn_id, timestamp=""):
    if not turn_id:
        return None
    recent = state.setdefault("request_turns", {})
    row = recent.get(turn_id)
    if row is None:
        row = dict(id=_turn_key(state["session_id"], turn_id), session_id=state["session_id"], turn_id=turn_id,
                   started_at=timestamp, started_inferred=True, ended_at="", status="unknown",
                   prompt_preview="", output_preview="", latest_output_preview="", input_hashes=[],
                   verified=False, first_turn=False, synthetic=str(turn_id).startswith("legacy-user:"),
                   **state.get("request_source", {}))
        recent[turn_id] = row
        while len(recent) > 32:
            oldest = next((key for key in recent if key != state.get("request_current") and key != turn_id), None)
            if oldest is None:
                break
            recent.pop(oldest)
    return row


def _turn_save(state, row, timestamp=""):
    if row is None or not row.get("verified"):
        return
    if not state.get("request_first_verified"):
        state["request_first_verified"] = row["turn_id"]
    row["first_turn"] = row["turn_id"] == state["request_first_verified"]
    row["observed_at"] = timestamp or row.get("observed_at") or row.get("started_at") or ""
    state.setdefault("request_updates", {})[row["id"]] = json.loads(json.dumps(row))


def _turn_activate(state, turn_id, timestamp, owned=False, explicit_start=False):
    current = state.get("request_current")
    if current and current != turn_id:
        old = _turn_get(state, current)
        if old and old.get("synthetic") and not old.get("has_usage") and not old.get("ended_at"):
            # A user message may precede the official turn ID in the same scan.
            replacement = dict(old, id=_turn_key(state["session_id"], turn_id), turn_id=turn_id, synthetic=False)
            state.setdefault("request_turns", {})[turn_id] = replacement
            old["alias_of"] = replacement["id"]
            if state.get("request_first_verified") == current:
                state["request_first_verified"] = turn_id
            _turn_save(state, old, timestamp)
        elif old and old.get("status") == "running":
            old["status"] = "unknown"
            _turn_save(state, old, timestamp)
    state["request_current"] = turn_id
    row = _turn_get(state, turn_id, timestamp)
    if row:
        row["verified"] = bool(row.get("verified") or owned)
        if explicit_start:
            if row.get("started_inferred") or not row.get("started_at"):
                row["started_at"] = timestamp
            row["started_inferred"] = False
            if not row.get("ended_at"):
                row["status"] = "running"
        _turn_save(state, row, timestamp)
    return row


def _request_message(entry):
    payload = entry.get("payload") or {}
    kind, subtype = entry.get("type"), payload.get("type")
    if kind == "response_item" and subtype in (None, "message") and payload.get("role") == "user":
        return payload.get("content", ""), "response"
    if kind == "event_msg" and subtype == "user_message":
        return payload.get("message", payload.get("content", "")), "event"
    if kind == "event_msg" and subtype in ("item_started", "item_completed"):
        item = payload.get("item") or {}
        if isinstance(item, dict) and item.get("type") in ("user_message", "userMessage", "UserMessage"):
            return item.get("content", item.get("message", "")), str(item.get("id") or "item")
    return None, ""


def _turn_before(entry, state):
    payload = entry.get("payload") or {}
    if _turn_inherited(entry, state):
        return
    kind, subtype = entry.get("type"), payload.get("type")
    timestamp = _timestamp(entry.get("timestamp"))
    official = ""
    if kind == "turn_context" or kind == "event_msg" and subtype in ("task_started", "turn_started"):
        official = str(payload.get("turn_id") or (payload.get("id") if kind == "turn_context" else "") or "")
    if official:
        row = _turn_activate(state, official, _timestamp(payload.get("started_at")) or timestamp,
                             _turn_owned(entry, state), kind == "event_msg")
        if row:
            row["synthetic"] = False
    message, representation = _request_message(entry)
    preview = _user_preview(message) if message is not None else ""
    if not preview:
        return
    fingerprint = _hash(_plain(message, 262144))
    current = _turn_get(state, state.get("request_current") or state.get("turn_id"), timestamp)
    last = state.get("request_last_input", {})
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    ident = payload.get("id") or item.get("id")
    same_identity = bool(ident and last.get("message_id") == ident and current and last.get("turn_id") == current["turn_id"])
    conflicting_ids = bool(ident and last.get("message_id") and ident != last["message_id"])
    paired = same_identity or (not conflicting_ids and last.get("hash") == fingerprint and last.get("representation") != representation
              and not state.get("request_usage_since_input") and current and not current.get("ended_at"))
    if not current or current.get("ended_at") and not paired or current.get("synthetic") and not paired:
        marker = ident or entry.get("ordinal", entry.get("_byte_offset", timestamp))
        turn_id = "legacy-user:" + _hash([state["session_id"], marker, timestamp, fingerprint])[:24]
        # A second genuine legacy input starts a separate request, even if its text repeats.
        if current and current.get("synthetic"):
            current["has_usage"] = True  # prevent the official-ID promotion path from joining two inputs
        current = _turn_activate(state, turn_id, timestamp, _turn_owned(entry, state), True)
    if current:
        current["verified"] = bool(current.get("verified") or _turn_owned(entry, state))
        if not current.get("ended_at"):
            current["status"] = "running"
        if not current.get("prompt_preview"):
            current["prompt_preview"] = preview
            current["started_at"] = min(filter(None, (current.get("started_at"), timestamp))) if timestamp else current.get("started_at")
            current["started_inferred"] = False
        hashes = current.setdefault("input_hashes", [])
        if fingerprint not in hashes:
            hashes.append(fingerprint)
            del hashes[:-32]
        _turn_save(state, current, timestamp)
    state["request_last_input"] = {"hash": fingerprint, "representation": representation, "message_id": ident,
                                   "turn_id": current.get("turn_id") if current else None}
    state["request_usage_since_input"] = False


def _agent_target(value, state):
    import posixpath
    value = str(value or "").strip()
    if not value or value.startswith("/") or UUID_RE.fullmatch(value):
        return value
    parent = state.get("request_source", {}).get("agent_path") or "/root"
    return posixpath.normpath(parent.rstrip("/") + "/" + value)


def _turn_agent_entry(entry, state, timestamp):
    payload = entry.get("payload") or {}
    kind = payload.get("type")
    if entry.get("type") != "response_item":
        return
    call_id = str(payload.get("call_id") or "")
    calls = state.setdefault("request_calls", {})
    if kind in ("function_call", "custom_tool_call"):
        name = str(payload.get("name") or "").split(".")[-1]
        parent_turn = state.get("request_current") or state.get("turn_id")
        if name not in ("spawn_agent", "followup_task", "send_input") or not call_id or not parent_turn:
            return
        try:
            args = payload.get("arguments", payload.get("input", {}))
            args = json.loads(args) if isinstance(args, str) else args
            if not isinstance(args, dict):
                return
        except ValueError:
            return
        calls[call_id] = dict(id="agent-link:" + _hash([state["session_id"], call_id]),
            parent_session_id=state["session_id"], parent_turn_id=parent_turn,
            kind="spawn" if name == "spawn_agent" else "followup", timestamp=timestamp,
            target=_agent_target(args.get("task_name") if name == "spawn_agent" else args.get("target", args.get("id")), state),
            message_hash=_hash(_plain(args.get("message", ""), 262144)), call_id=call_id)
        while len(calls) > 64:
            calls.pop(next(iter(calls)))
    elif kind in ("function_call_output", "custom_tool_call_output") and call_id in calls:
        link = calls.pop(call_id)
        output = payload.get("output")
        text = output if isinstance(output, str) else _plain(output, 32000)
        try:
            value = json.loads(text) if text else output
        except ValueError:
            value = None
        if isinstance(value, dict):
            if value.get("isError") or value.get("error") or value.get("status") in ("error", "failed"):
                return
            child_id = value.get("agent_id") or value.get("thread_id")
            if child_id:
                link["child_session_id"] = str(child_id)
            if value.get("task_name"):
                link["target"] = _agent_target(value["task_name"], state)
            if value.get("turn_id"):
                link["child_turn_id"] = str(value["turn_id"])
        elif not text or text.lstrip().lower().startswith(("error", "failed", "错误")):
            return
        if link.get("kind") == "spawn" and not isinstance(value, dict):
            return  # A failed/unrecognizable spawn is not evidence of child ownership.
        state.setdefault("request_link_updates", {})[link["id"]] = link


def _turn_after(entry, state, records):
    if _turn_inherited(entry, state):
        return
    payload = entry.get("payload") or {}
    timestamp = _timestamp(entry.get("timestamp"))
    for record in records:
        explicit = payload.get("turn_id") if entry.get("type") == "token_usage_record" else None
        current = state.get("request_current")
        if not record.get("request_turn_id"):
            if explicit or record.get("session_id") != state["session_id"]:
                request_turn = record.get("turn_id")
            elif entry.get("type") == "response_item":
                request_turn = record.get("turn_id")
            else:
                request_turn = current or record.get("turn_id")
            if request_turn:
                record["request_turn_id"] = str(request_turn)
                _remember_preview_record(state, record)
        if not record.get("context_owner_verified") or record.get("session_id") != state["session_id"]:
            continue
        row = _turn_get(state, record.get("request_turn_id") or record.get("turn_id"), record.get("timestamp"))
        if row:
            row["verified"] = row["has_usage"] = True
            if not row.get("prompt_preview"):
                row["prompt_preview"] = record.get("prompt_preview", "")
            _turn_save(state, row, timestamp)
            state["request_usage_since_input"] = True
    message_meta = payload.get("internal_chat_message_metadata_passthrough") or {}
    message_meta = message_meta if isinstance(message_meta, dict) else {}
    related = state.get("preview_recent", {}).get(str(payload.get("response_id") or entry.get("response_id") or ""))
    implicit = state.get("request_current") or state.get("turn_id") or ""
    active = state.get("request_turns", {}).get(implicit, {})
    if active.get("synthetic") and not active.get("has_usage") and state.get("turn_id"):
        # Late unlabelled output still belongs to the declared old turn until
        # the new user request has its own context or accounting evidence.
        implicit = state["turn_id"]
    turn_id = str(payload.get("turn_id") or message_meta.get("turn_id") or ((related.get("request_turn_id") or related.get("turn_id")) if related and related.get("context_owner_verified") else "")
                  or implicit)
    row = _turn_get(state, turn_id, timestamp)
    if row:
        owner = payload.get("thread_id") or message_meta.get("thread_id")
        text = _assistant_preview(payload) if entry.get("type") == "response_item" and (not owner or owner == state["session_id"]) else ""
        if entry.get("type") == "event_msg" and payload.get("type") == "agent_message":
            text = _assistant_preview(dict(payload, type="message", role="assistant", content=payload.get("message", "")))
        if text:
            row["latest_output_preview"] = text
            if payload.get("phase") in ("final", "final_answer") or payload.get("channel") == "final":
                row.update(output_preview=text, ended_at=timestamp, status="completed")
        if entry.get("type") == "event_msg" and payload.get("type") in ("task_complete", "turn_complete", "turn_aborted"):
            row["ended_at"] = _timestamp(payload.get("completed_at")) or timestamp
            row["status"] = "aborted" if payload["type"] == "turn_aborted" else "completed"
            duration = _duration_ms(payload.get("duration_ms"))
            if duration is not None:
                row["duration_ms"] = duration
            start = _timestamp(payload.get("started_at"))
            if start and row.get("started_inferred"):
                row.update(started_at=start, started_inferred=False)
            final = _plain(payload.get("last_agent_message"), PREVIEW_LIMIT)
            if final:
                row["output_preview"] = final
        _turn_save(state, row, timestamp)
    _turn_agent_entry(entry, state, timestamp)


def process_entry(entry: dict, state: dict, context: _Context,
                  source_id="local", source_name="本机", account_since=None):
    if not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict):
        return [], []
    if entry.get("type") == "session_meta":
        records, observations = _process_accounting_entry(entry, state, context, source_id, source_name, account_since)
        if not state.get("request_source_set"):
            _turn_source(state, entry["payload"])
            state["request_source_set"] = True
        return records, observations
    _turn_before(entry, state)
    records, observations = _process_accounting_entry(entry, state, context, source_id, source_name, account_since)
    _turn_after(entry, state, records)
    return records, observations


def _drain_request_updates(state, origin):
    turns = [dict(row, _origin=origin) for row in state.pop("request_updates", {}).values()]
    links = [dict(row, _origin=origin) for row in state.pop("request_link_updates", {}).values()]
    return turns, links


def _diagnose(state, name):
    counts = state.setdefault("diagnostics", {})
    counts[name] = counts.get(name, 0) + 1


def _parent_id(meta) -> str:
    source = meta.get("source")
    spawned = ""
    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict):
            spawn = subagent.get("thread_spawn")
            if isinstance(spawn, dict):
                spawned = spawn.get("parent_thread_id") or ""
    return str(meta.get("forked_from_id") or spawned or "")


class _Context:
    def __init__(self, files, titles):
        self.index = {_rollout_id(p): p for p in files}
        self.parents = {}
        self.titles = titles

    def parent_signatures(self, parent, cutoff):
        key = (parent, cutoff)
        if key in self.parents:
            return self.parents[key]
        path = self.index.get(parent)
        if path is None:
            return None
        result = []
        try:
            with path.open("rb") as stream:
                for raw in stream:
                    if b'"token_count"' not in raw or not raw.endswith(b"\n"):
                        continue
                    try:
                        entry = json.loads(raw)
                        if cutoff and _timestamp(entry.get("timestamp")) > cutoff:
                            continue
                        payload = entry.get("payload", {})
                        if entry.get("type") != "event_msg" or payload.get("type") != "token_count":
                            continue
                        info = payload.get("info") or {}
                        total, last = _usage(info.get("total_token_usage")), _usage(info.get("last_token_usage"))
                        result.append(_signature(total, last))
                    except (ValueError, AttributeError, TypeError):
                        continue
        except OSError:
            return None
        self.parents[key] = result
        return result


def _record(state, usage, timestamp, source_id, source_name, context, quality,
            ident, response_id=None, session_id=None, turn_id=None):
    session_id = str(session_id or state["session_id"])
    data = dict(zip(COUNTERS, usage))
    data["reported_total_tokens"] = usage[5]
    data["total_tokens"] = usage[0] + usage[3]
    if usage[1] + usage[2] > usage[0] or usage[4] > usage[3]:
        quality += ":invalid_subsets"
    effective_turn = str(turn_id or state.get("turn_id") or "")
    preview = state.get("prompt_preview", "")[:PREVIEW_LIMIT]
    owns_context = session_id == state.get("session_id") and not (state.get("turn_id") and effective_turn != state.get("turn_id"))
    output_preview = _take_output_preview(state, response_id, effective_turn)
    if not owns_context:
        preview = output_preview = ""
    if quality.startswith("cumulative"):
        output_preview = ""
    data.update(id=ident, response_id=response_id, session_id=session_id,
                turn_id=effective_turn, timestamp=timestamp,
                model=state.get("model") or "unknown", service_tier=state.get("service_tier"),
                source_id=source_id, source_name=source_name, provider=state.get("provider") or "unknown",
                limit_id=state.get("limit_id"), quality=quality,
                session_title=context.titles.get(session_id, ""), prompt_preview=preview,
                output_preview=output_preview,
                context_owner_verified=owns_context)
    if not owns_context:
        data.update(model="unknown", service_tier=None, limit_id=None)
    _remember_preview_record(state, data)
    return data


def _observations(payload, timestamp, source_id, state, account_since):
    rate = payload.get("rate_limits")
    if not isinstance(rate, dict) or not timestamp:
        return []
    limit_id = rate.get("limit_id") or "codex"
    state["limit_id"] = limit_id
    if rate.get("plan_type"):
        state["plan_type"] = str(rate["plan_type"])
    result = []
    for slot in ("primary", "secondary"):
        window = rate.get(slot)
        if not isinstance(window, dict):
            continue
        used = window.get("used_percent")
        minutes = window.get("window_minutes", window.get("window_duration_mins"))
        resets = window.get("resets_at")
        if (isinstance(used, bool) or not isinstance(used, (int, float))
                or not math.isfinite(used) or not 0 <= used <= 100
                or not isinstance(minutes, int) or minutes <= 0
                or not isinstance(resets, int)):
            continue
        data = dict(timestamp=timestamp, used_percent=used, window_minutes=minutes,
                    resets_at=resets, plan_type=state.get("plan_type"), limit_id=limit_id,
                    source_id=source_id, account_key="current" if account_since and timestamp >= account_since else "unknown")
        data["id"] = "observation:" + _hash(data)
        result.append(data)
    return result


def _process_accounting_entry(entry: dict, state: dict, context: _Context,
                  source_id="local", source_name="本机", account_since=None):
    """Stateful pure frame transform; its JSON state survives incremental scans."""
    if not isinstance(entry, dict):
        return [], []
    kind, payload = entry.get("type"), entry.get("payload")
    if not isinstance(payload, dict):
        return [], []
    timestamp = _timestamp(entry.get("timestamp"))
    if kind == "session_meta" and not state.get("meta_seen"):
        state.update(meta_seen=True, session_id=str(payload.get("id") or payload.get("thread_id") or state["session_id"]),
                     provider=str(payload.get("model_provider") or "unknown"),
                     parent_id=_parent_id(payload), history_mode=payload.get("history_mode", "legacy"),
                     history_base=payload.get("history_base"),
                     history_start=payload.get("subagent_history_start_ordinal"),
                     fork_timestamp=timestamp)
        state["replay_done"] = bool(state.get("history_base") or isinstance(state.get("history_start"), int) or not state["parent_id"])
        return [], []

    ordinal, boundary = entry.get("ordinal"), state.get("history_start")
    inherited = isinstance(boundary, int) and isinstance(ordinal, int) and ordinal < boundary
    subtype = payload.get("type")
    if kind == "turn_context":
        model = payload.get("model") or (payload.get("info") or {}).get("model")
        if isinstance(model, str) and model.strip():
            state["model"] = model.strip()
        turn_id = str(payload.get("turn_id") or payload.get("id") or "")
        if turn_id and turn_id != state.get("turn_id"):
            state.update(turn_id=turn_id, prompt_preview="", modern_candidate=None, active_response_id=None, preview_pending=[])
        if isinstance(payload.get("service_tier"), str):
            state["service_tier"] = payload["service_tier"]
        return [], []
    if kind == "event_msg":
        if subtype == "thread_settings_applied":
            settings = payload.get("thread_settings") or payload.get("settings") or {}
            if isinstance(settings, dict) and settings.get("service_tier"):
                state["service_tier"] = str(settings["service_tier"])
        elif subtype in ("task_started", "turn_started"):
            new_turn = str(payload.get("turn_id") or "")
            if new_turn and new_turn != state.get("turn_id"):
                state.update(turn_id=new_turn, prompt_preview="", modern_candidate=None, active_response_id=None, preview_pending=[])
        elif subtype == "user_message" and not inherited:
            preview = _user_preview(payload.get("message", payload.get("content", "")))
            if preview:
                state["prompt_preview"] = preview
        elif subtype in ("item_completed", "item_started") and not inherited:
            item = payload.get("item") or {}
            if isinstance(item, dict) and item.get("type") in ("user_message", "userMessage", "UserMessage"):
                preview = _user_preview(item.get("content", item.get("message", "")))
                if preview:
                    state["prompt_preview"] = preview
        elif subtype == "agent_message" and not inherited:
            message = dict(payload, type="message", role="assistant", content=payload.get("message", ""))
            return _capture_output(entry, message, state), []
        elif subtype in ("task_complete", "turn_complete", "turn_aborted"):
            # A message after the final meter without an explicit response ID
            # cannot be safely attributed. Do not carry it into the next turn.
            state["preview_pending"] = []
        elif subtype == "raw_response_completed":
            response = payload.get("response_id")
            if response and response != state.get("active_response_id"):
                state.update(active_response_id=response, modern_candidate=None)
            if response and not inherited:
                pending = state.get("preview_pending", [])
                for item in pending:
                    if not item.get("response_id"):
                        item["response_id"] = str(response)
                old = state.get("preview_recent", {}).get(str(response))
                if old and old.get("context_owner_verified"):
                    preview = _take_output_preview(state, str(response), old.get("turn_id"))
                    if preview:
                        record = dict(old, output_preview=" ".join(dict.fromkeys(filter(None, (old.get("output_preview"), preview))))[:PREVIEW_LIMIT])
                        _remember_preview_record(state, record)
                        candidate = state.get("modern_candidate")
                        if candidate and candidate.get("response_id") == response:
                            candidate["record"] = record
                        return [record], []

    if kind == "response_item" and not inherited and payload.get("role") == "user" and payload.get("type", "message") == "message":
        if not state.get("prompt_preview"):
            preview = _user_preview(payload.get("content", ""))
            if preview:
                state["prompt_preview"] = preview

    if kind == "response_item" and not inherited and payload.get("role") == "assistant":
        return _capture_output(entry, payload, state), []

    if kind == "token_usage_record":
        raw = payload.get("usage")
        usage = _usage(raw)
        if not _billable(raw, usage) or not timestamp:
            _take_output_preview(state, str(payload.get("response_id") or ""),
                                 str(payload.get("turn_id") or state.get("turn_id") or ""))
            _diagnose(state, "invalid_modern")
            return [], []
        response = str(payload.get("response_id") or "")
        owner = str(payload.get("thread_id") or state["session_id"])
        turn = str(payload.get("turn_id") or state.get("turn_id") or "")
        # These fields survive copied fork history and identify the original
        # response even when its containing physical file belongs to a child.
        state["modern_candidate"] = {"usage": usage, "response_id": response, "thread_id": owner,
                                     "turn_id": turn, "matched": False}
        state["active_response_id"] = response or None
        if inherited:
            state["preview_pending"] = []
            return [], []
        ident = "response:{}:{}".format(owner, response) if response else "modern:" + _hash([state["rollout_id"], timestamp, usage, turn])
        record = _record(state, usage, timestamp, source_id, source_name, context, "response", ident,
                         response or None, owner, turn)
        # A preceding quota refresh can refer to another model's bucket. Only
        # the paired legacy snapshot may attach a bucket to this response.
        record["limit_id"] = None
        record.update(_call_timing(payload))
        _remember_preview_record(state, record)
        state["modern_candidate"]["record"] = record
        return [record], []

    if kind != "event_msg" or subtype != "token_count":
        return [], []
    observations = [] if inherited else _observations(payload, timestamp, source_id, state, account_since)
    info = payload.get("info")
    if not isinstance(info, dict):
        return [], observations
    total_raw, last_raw = info.get("total_token_usage"), info.get("last_token_usage")
    total, last = _usage(total_raw), _usage(last_raw)
    signature = _signature(total, last)
    source = str((payload.get("rate_limits") or {}).get("limit_id") or "unknown")
    previous_by_source = state.setdefault("last_by_source", {})
    duplicate = total is not None and (previous_by_source.get(source) == signature or state.get("previous_signature") == signature)
    previous_by_source[source] = signature
    state["previous_signature"] = signature
    old_total = state.setdefault("last_totals", {}).get(source)
    if total is not None:
        state["last_totals"][source] = total

    # Synthetic context-size notifications are not responses and do not spend
    # their total_tokens. A full placeholder can reset the legacy baseline.
    synthetic = last is not None and last[0] + last[1] + last[2] + last[3] + last[4] == 0 and last[5] > 0
    high = state.get("high_water")
    reset = (total is not None and _billable(last_raw, last) and total == last and old_total is not None
             and all(a <= b for a, b in zip(total[:5], old_total[:5]))
             and any(a < b for a, b in zip(total[:5], old_total[:5])))
    if total is not None:
        if reset or (synthetic and not any(total[:5])):
            state["high_water"] = total
        else:
            state["high_water"] = [max(a, b) for a, b in zip(high, total)] if high is not None else total

    replay = inherited
    if not state.get("replay_done") and state.get("parent_id"):
        signatures = context.parent_signatures(state["parent_id"], state.get("fork_timestamp"))
        if signatures is None:
            state["pending_parent"] = state["parent_id"]
            replay = True
        else:
            state.pop("pending_parent", None)
            try:
                position = signatures.index(signature, state.get("parent_position", 0))
                state["parent_position"] = position + 1
                replay = True
            except ValueError:
                state["replay_done"] = True
    if duplicate or synthetic or replay:
        if replay:
            state["preview_pending"] = []
        _diagnose(state, "duplicate" if duplicate else "context_only" if synthetic else "inherited")
        return [], [] if replay else observations

    candidate = state.get("modern_candidate")
    if candidate and not candidate.get("matched"):
        same_turn = not state.get("turn_id") or candidate["turn_id"] == state.get("turn_id")
        # Comparing usage, not cumulative baselines, is essential after resume
        # and when the new accounting stream was introduced into an old thread.
        if same_turn and (last == candidate["usage"] or not _billable(last_raw, last)):
            candidate["matched"] = True
            _diagnose(state, "modern_legacy_pair")
            # The quota bucket often appears only in the later legacy notice.
            # Enrich the existing response rather than adding another request.
            record = candidate.get("record")
            paired_rate = payload.get("rate_limits")
            paired_limit = paired_rate.get("limit_id") if isinstance(paired_rate, dict) else None
            if record and record.get("context_owner_verified") and paired_limit and record.get("limit_id") != paired_limit:
                record = dict(record, limit_id=paired_limit)
                candidate["record"] = record
                _remember_preview_record(state, record)
                return [record], observations
            return [], observations

    model = info.get("model") or info.get("model_name") or payload.get("model")
    if isinstance(model, str) and model.strip():
        state["model"] = model.strip()
    if _billable(last_raw, last):
        usage, quality = last, "legacy_last"
    elif total is not None and not synthetic:
        if high is None:
            if state.get("history_base") or state.get("parent_id"):
                _diagnose(state, "missing_baseline")
                return [], observations
            usage, quality = total, "cumulative_observation"
        elif old_total is not None and any(a > b for a, b in zip(old_total[:5], high[:5])):
            # A different lane can outlive a reset of the active baseline. Its
            # old cumulative value must not suddenly become a giant new spend.
            _diagnose(state, "ambiguous_cumulative")
            return [], observations
        elif all(a >= b for a, b in zip(total[:5], high[:5])):
            usage = [max(0, a - b) for a, b in zip(total, high)]
            quality = "cumulative_delta"
        else:
            _diagnose(state, "ambiguous_cumulative")
            return [], observations
    else:
        return [], observations
    if usage[0] + usage[3] == 0 or not timestamp:
        return [], observations
    ident = "legacy:" + _hash([state["rollout_id"], timestamp, signature])
    response_id = state.get("active_response_id") if state.get("modern_candidate") is None else None
    if response_id:
        state["active_response_id"] = None
    return [_record(state, usage, timestamp, source_id, source_name, context, quality, ident,
                    response_id)], observations


def _valid_cursor(stream, cursor, stat) -> bool:
    if not cursor or cursor.get("version") != PARSER_VERSION:
        return False
    offset = cursor.get("offset", 0)
    if not isinstance(offset, int) or offset > stat.st_size:
        return False
    if cursor.get("identity") != [stat.st_dev, stat.st_ino]:
        return False
    if stat.st_size == offset and cursor.get("mtime_ns") != stat.st_mtime_ns:
        return False
    prefix_len = cursor.get("prefix_len", 0)
    stream.seek(0)
    if _hash(stream.read(prefix_len)) != cursor.get("prefix_hash"):
        return False
    stream.seek(max(0, offset - 1024))
    return _hash(stream.read(min(offset, 1024))) == cursor.get("tail_hash")


def _checkpoint(stream, path, offset, state):
    stat = path.stat()
    prefix_len = min(1024, offset)
    stream.seek(0)
    prefix_hash = _hash(stream.read(prefix_len))
    stream.seek(max(0, offset - 1024))
    tail_hash = _hash(stream.read(min(offset, 1024)))
    stream.seek(offset)
    return {"version": PARSER_VERSION, "offset": offset, "size": stat.st_size,
            "identity": [stat.st_dev, stat.st_ino],
            "mtime_ns": stat.st_mtime_ns, "prefix_len": prefix_len, "prefix_hash": prefix_hash,
            "tail_hash": tail_hash, "state": state, "path": str(path), "last_scan_at": _now()}


def iter_scan(root, get_cursor, source_id="local", source_name="本机", account_since=None, stop=None):
    """Yield transactional batches. Complete-line offsets never cross a partial tail."""
    root = Path(root).expanduser()
    files = session_files(root)
    context = _Context(files, read_session_titles(root))
    yield {"titles": context.titles, "files": len(files)}
    account_since = _timestamp(account_since) or None
    for path in files:
        if _stopped(stop):
            break
        key = cursor_key(root, path, source_id)
        cursor = get_cursor(key)
        try:
            stat = path.stat()
            pending = (cursor or {}).get("state", {}).get("pending_parent")
            parent_available = bool(pending and pending in context.index)
            with path.open("rb") as stream:
                valid = _valid_cursor(stream, cursor, stat) and not parent_available
                if valid and cursor["offset"] == stat.st_size and cursor.get("mtime_ns") == stat.st_mtime_ns:
                    yield {"indexed_file": True, "key": key, "diagnostics": cursor["state"].get("diagnostics", {}),
                           "pending_parent": cursor["state"].get("pending_parent")}
                    continue
                state = json.loads(json.dumps(cursor["state"])) if valid else _initial_state(path)
                offset = cursor["offset"] if valid else 0
                generation = cursor.get("generation") if valid else None
                generation = generation or _hash([stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size, _now()])[:24]
                reconcile_pending = bool(cursor.get("reconcile_pending")) if valid else True
                origin = {"file_key": key, "generation": generation}
                stream.seek(offset)
                records, observations, bytes_read, total_bytes = [], [], 0, 0
                while not _stopped(stop):
                    raw = stream.readline()
                    if not raw or not raw.endswith(b"\n"):
                        break
                    offset = stream.tell()
                    bytes_read += len(raw)
                    total_bytes += len(raw)
                    # Decode only message items for the authorized previews;
                    # tool results and hidden reasoning response items bypass it.
                    is_message_item = (b'"response_item"' in raw and (
                        any(marker in raw for marker in (b'"role":"user"', b'"role": "user"'))
                        or ((b'"type":"message"' in raw or b'"type": "message"' in raw)
                            and any(marker in raw for marker in (b'"role":"assistant"', b'"role": "assistant"')))))
                    is_agent_routing = b'"response_item"' in raw and (
                        any(name in raw for name in (b'spawn_agent', b'followup_task', b'send_input'))
                        or (b'call_output"' in raw and any(call_id.encode("utf-8") in raw for call_id in state.get("request_calls", {}))))
                    if is_message_item or is_agent_routing or any(marker in raw for marker in (b'"token_usage_record"', b'"event_msg"', b'"turn_context"', b'"session_meta"')):
                        try:
                            entry = json.loads(raw)
                            entry["_byte_offset"] = offset - len(raw)
                            new_records, new_observations = process_entry(entry, state, context, source_id, source_name, account_since)
                            records.extend(dict(row, _origin=origin) for row in new_records)
                            observations.extend(dict(row, _origin=origin) for row in new_observations)
                        except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
                            _diagnose(state, "malformed_lines")
                    if bytes_read >= 2 * 1024 * 1024 or len(records) + len(observations) >= 1000:
                        turns, agent_links = _drain_request_updates(state, origin)
                        checkpoint = _checkpoint(stream, path, offset, state)
                        checkpoint.update(generation=generation, reconcile_pending=reconcile_pending, scan_complete=False)
                        yield {"key": key, "cursor": checkpoint, "records": records, "observations": observations,
                               "bytes_read": bytes_read, "turns": turns, "agent_links": agent_links}
                        records, observations, bytes_read = [], [], 0
                turns, agent_links = _drain_request_updates(state, origin)
                checkpoint = _checkpoint(stream, path, offset, state)
                complete = offset == checkpoint["size"] and not _stopped(stop)
                checkpoint.update(generation=generation, reconcile_pending=reconcile_pending,
                                  scan_complete=complete and not state.get("pending_parent") and not state.get("diagnostics", {}).get("malformed_lines"))
                yield {"key": key, "cursor": checkpoint, "records": records, "observations": observations,
                       "bytes_read": bytes_read, "indexed_file": complete, "partial_file": not complete,
                       "turns": turns, "agent_links": agent_links,
                       "diagnostics": state.get("diagnostics", {}),
                       "pending_parent": state.get("pending_parent")}
        except OSError as error:
            yield {"error": type(error).__name__, "key": key}


def scan_directory(root, cursors=None, source_id="local", source_name="本机", account_since=None, stop=None):
    """Serializable stdlib-only transport interface used by the SSH collector."""
    cursors = dict(cursors or {})
    result = {"records": [], "observations": [], "turns": [], "agent_links": [], "cursors": cursors, "titles": {}, "files": 0,
              "bytes_read": 0, "errors": [], "deferred_files": 0, "partial_files": 0, "diagnostics": {}, "reconciliations": []}
    for batch in iter_scan(root, cursors.get, source_id, source_name, account_since, stop):
        if "titles" in batch:
            result.update(titles=batch["titles"], files=batch["files"])
        result["records"].extend(batch.get("records", []))
        result["observations"].extend(batch.get("observations", []))
        result["turns"].extend(batch.get("turns", []))
        result["agent_links"].extend(batch.get("agent_links", []))
        result["bytes_read"] += batch.get("bytes_read", 0)
        if "cursor" in batch:
            checkpoint = batch["cursor"]
            if checkpoint.get("scan_complete") and checkpoint.get("reconcile_pending"):
                result["reconciliations"].append({"file_key": batch["key"], "generation": checkpoint["generation"]})
                checkpoint = dict(checkpoint, reconcile_pending=False)
            cursors[batch["key"]] = checkpoint
        if batch.get("error"):
            result["errors"].append(batch["error"])
        if batch.get("indexed_file"):
            result["deferred_files"] += int(bool(batch.get("pending_parent")))
            for name, count in batch.get("diagnostics", {}).items():
                result["diagnostics"][name] = result["diagnostics"].get(name, 0) + count
        result["partial_files"] += int(bool(batch.get("partial_file")))
    return result


class Collector:
    def __init__(self, store):
        self.store = store

    def scan(self, root: Path, source_id="local", source_name="本机", account_since=None, stop=None) -> dict:
        root = Path(root).expanduser()
        status = {"changed": 0, "files": 0, "indexed_files": 0, "bytes_read": 0, "deferred_files": 0, "partial_files": 0, "diagnostics": {},
                  "error": None, "last_scan_at": _now(), "source_id": source_id, "source_name": source_name}
        if not root.is_dir():
            status["error"] = "Codex 数据目录不存在"
        else:
            for batch in iter_scan(root, self.store.get_cursor, source_id, source_name, account_since, stop):
                if "titles" in batch:
                    status["files"] = batch["files"]
                    status["changed"] += self.store.update_session_titles(batch["titles"])
                if "cursor" in batch:
                    status["changed"] += self.store.commit_batch(batch["records"], batch["observations"], source_id, batch["key"], batch["cursor"],
                                                                turns=batch.get("turns", []), agent_links=batch.get("agent_links", []))
                status["bytes_read"] += batch.get("bytes_read", 0)
                if batch.get("indexed_file"):
                    status["indexed_files"] += 1
                    status["deferred_files"] += int(bool(batch.get("pending_parent")))
                    for name, count in batch.get("diagnostics", {}).items():
                        status["diagnostics"][name] = status["diagnostics"].get(name, 0) + count
                if batch.get("error"):
                    status["error"] = "部分会话文件暂时无法读取（{}）".format(batch["error"])
                status["partial_files"] += int(bool(batch.get("partial_file")))
        status["last_scan_at"] = _now()
        status["cancelled"] = _stopped(stop)
        if not status["error"] and (status["deferred_files"] or status["diagnostics"].get("ambiguous_cumulative") or status["diagnostics"].get("missing_baseline") or status["diagnostics"].get("malformed_lines")):
            status["error"] = "部分历史计量无法确认，已保留可核验的用量"
        if not status["error"] and (status["cancelled"] or status["partial_files"]):
            status["error"] = "采集尚未完成，等待下次增量扫描"
        status["status"] = "error" if status["error"] else "ok"
        status["name"] = source_name
        self.store.set_source_status(source_id, **{k: v for k, v in status.items() if k != "source_id"})
        return status
