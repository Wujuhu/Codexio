"""Bounded, on-demand original user text and final assistant reply for iPhone."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from codexio.usage_collector import _visible_message
from codexio.usage_store import UsageStore


def _started(value: str) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _registered_file(db, session: str, roots: list[str]) -> Path | None:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", session or ""):
        return None
    for raw, in db.execute("SELECT data FROM usage_cursors WHERE key LIKE ? LIMIT 8", ("usage:local:%:"+session.lower(),)):
        try:
            value = json.loads(raw)
            file = Path(value["path"])
            actual = file.resolve(strict=True)
            if file.is_symlink() or actual.suffix != ".jsonl" or not actual.is_file() or actual.stat().st_size > 64*1024*1024:
                continue
            for path in roots:
                root = Path(path).expanduser().resolve(strict=True)
                if (root / "sessions") in actual.parents or (root / "archived_sessions") in actual.parents:
                    return actual
        except (OSError, TypeError, ValueError, KeyError):
            continue
    return None


def _recover(file: Path, session: str, turns: set[str]):
    user = final = ""
    user_complete = final_complete = False
    attachments = []
    current = ""
    with file.open("rb") as source:
        for raw in source:
            if len(raw) > 2*1024*1024:
                continue
            if not any(marker in raw for marker in (b'user_message', b'"message"', b'last_agent_message', b'turn_context', b'task_started', b'turn_started')):
                continue
            try:
                entry = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            kind, subtype = entry.get("type"), payload.get("type")
            if kind == "turn_context" or kind == "event_msg" and subtype in ("task_started", "turn_started"):
                current = str(payload.get("turn_id") or payload.get("id") or current)
            turn = str(payload.get("turn_id") or current)
            if turn not in turns:
                continue
            if kind == "event_msg" and subtype == "user_message":
                value = _visible_message(payload.get("message", payload.get("content")), user=True)
                if len(value["text"]) > len(user):
                    user, user_complete, attachments = value["text"], value["complete"], value["attachments"]
            elif kind == "response_item" and payload.get("role") == "user":
                value = _visible_message(payload.get("content"), user=True)
                if len(value["text"]) > len(user):
                    user, user_complete, attachments = value["text"], value["complete"], value["attachments"]
            elif kind == "response_item" and payload.get("role") == "assistant" and (
                    payload.get("phase") in ("final", "final_answer") or payload.get("channel") == "final"):
                value = _visible_message(payload.get("content"))
                if value["text"]:
                    final, final_complete = value["text"], value["complete"]
            elif kind == "event_msg" and subtype in ("task_complete", "turn_complete"):
                value = _visible_message(payload.get("last_agent_message"))
                if value["text"]:
                    final, final_complete = value["text"], value["complete"]
    return dict(user=user, user_complete=user_complete, final=final, final_complete=final_complete,
                attachments=attachments[:6])


def _thumbnail(source: str) -> str | None:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PySide6.QtGui import QImageReader
    try:
        file = Path(source)
        if not file.is_absolute() or file.is_symlink() or not file.is_file() or file.stat().st_size > 12*1024*1024:
            return None
        reader = QImageReader(str(file))
        size = reader.size()
        if not size.isValid() or max(size.width(), size.height()) > 16384 or size.width()*size.height() > 40_000_000:
            return None
        ratio = min(1.0, 240/max(size.width(), size.height()))
        reader.setScaledSize(QSize(max(1, round(size.width()*ratio)), max(1, round(size.height()*ratio))))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return None
        blob = QByteArray()
        buffer = QBuffer(blob)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        saved = image.save(buffer, "JPEG", 55)
        buffer.close()
        content = bytes(blob)
        return base64.b64encode(content).decode("ascii") if saved and len(content) <= 12_288 else None
    except (OSError, ValueError):
        return None


def request_detail(query_path: str | Path, request_id: str, roots: list[str], *, recover=True, include_thumbnails=True) -> dict | None:
    """Read one real request and at most one already indexed 64 MiB rollout."""
    if not request_id or len(request_id.encode("utf-8")) > 512:
        return None
    path = Path(query_path).resolve()
    db = sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=30)
    try:
        result = db.execute("SELECT data FROM usage_request_groups WHERE id=? AND record_kind='user_request' AND is_subagent=0", (request_id,)).fetchone()
        if result is None:
            return None
        group = json.loads(result[0])
        if not any(str(source or "") == "local" or str(source or "").startswith("local:") for source in group.get("source_ids") or [group.get("source_id")]):
            return None
        session = str(group.get("session_id") or "")
        turn = str(group.get("turn_id") or "")
        turns = {turn}
        ordered = [(str(group.get("timestamp") or ""), turn)]
        for raw, in db.execute("SELECT data FROM usage_query_turns WHERE json_extract(data,'$.session_id')=? "
                               "AND (json_extract(data,'$.continuation_of') IN (?,?) "
                               "OR json_extract(data,'$.root_turn_id') IN (?,?)) LIMIT 64",
                               (session, request_id, turn, request_id, turn)):
            row = json.loads(raw)
            if not row.get("is_subagent") and row.get("turn_id"):
                turns.add(str(row["turn_id"]))
                ordered.append((str(row.get("started_at") or ""), str(row["turn_id"])))
        values = []
        for _stamp, member in sorted(set(ordered))[:64]:
            ident = "turn:%s:%s" % (session, member)
            row = db.execute("SELECT data FROM usage_request_messages WHERE id=?", (ident,)).fetchone()
            if row is not None:
                values.append(json.loads(row[0]))
        first = next((value for value in values if value.get("user")), {})
        last = next((value for value in reversed(values) if value.get("final")), {})
        detail = dict(user=first.get("user") or "", user_complete=bool(first.get("user_complete")),
                      final=last.get("final") or "", final_complete=bool(last.get("final_complete")),
                      attachments=list(first.get("attachments") or [])[:6])
        file = _registered_file(db, session, roots) if recover else None
    finally:
        db.close()
    if file and (not detail["user_complete"] or group.get("request_status") == "completed" and not detail["final_complete"]):
        recovered = _recover(file, session, turns)
        patch = {}
        for field in ("user", "final"):
            if recovered[field] and (not detail[field] or len(recovered[field]) > len(detail[field])):
                detail[field], detail[field+"_complete"] = recovered[field], recovered[field+"_complete"]
                patch[field], patch[field+"_complete"] = detail[field], detail[field+"_complete"]
        if recovered["attachments"]:
            detail["attachments"] = recovered["attachments"][:6]
            patch["attachments"] = detail["attachments"]
        if patch:
            UsageStore(path).save_messages([dict(patch, id="turn:%s:%s" % (session, turn))])
    started = _started(group.get("timestamp"))
    completed = _started(group.get("ended_at")) or None
    if started <= 0 or max(started, completed or started) < datetime.now().timestamp()-7*86_400:
        return None
    attachments = []
    thumbnails = 0
    for entry in detail["attachments"][:6]:
        name = str(entry.get("name") or "附件")[:240]
        mime = str(entry.get("mime") or "")[:80]
        image = _thumbnail(str(entry.get("path") or "")) if include_thumbnails and thumbnails < 2 and mime.startswith("image/") else None
        thumbnails += image is not None
        attachments.append(dict(id=hashlib.sha256((str(entry.get("id") or "")+name).encode()).hexdigest(),
                                name=name, mime=mime or None, thumbnail=image))
    value = dict(id=hashlib.sha256(request_id.encode()).hexdigest(), started=started, completed=completed,
                 status=group.get("request_status") or "completed", user=detail["user"], final=detail["final"],
                 userComplete=detail["user_complete"], finalComplete=detail["final_complete"],
                 availability="available" if detail["user_complete"] and (not detail["final"] or detail["final_complete"]) else "partial" if detail["user"] or detail["final"] else "unavailable",
                 attachments=attachments, full=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 1_048_576:
        value.update(user=value["user"].encode("utf-8")[:1200].decode("utf-8", "ignore"),
                     final=value["final"].encode("utf-8")[:5000].decode("utf-8", "ignore"),
                     userComplete=False, finalComplete=False, availability="capacity",
                     attachments=[dict(entry, thumbnail=None) for entry in attachments])
    return value
