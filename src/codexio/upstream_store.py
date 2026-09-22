"""Response-model observations, separate from billing and the usage ledger."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3
import time


class UpstreamStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, response_id, model, event="response.completed"):
        if not all(isinstance(v, str) and 0 < len(v.strip()) <= 256 for v in (response_id, model)):
            return
        response_id, model = response_id.strip(), model.strip()
        rank = 1 if event == "response.created" else 2
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=1) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS observations(response_id TEXT PRIMARY KEY,model TEXT NOT NULL,event TEXT NOT NULL,rank INTEGER NOT NULL,observed_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS revision(id INTEGER PRIMARY KEY,value INTEGER NOT NULL)")
            db.execute("INSERT OR IGNORE INTO revision VALUES(1,0)")
            old = db.execute("SELECT model,rank FROM observations WHERE response_id=?", (response_id,)).fetchone()
            if old and (old[1] > rank or old == (model, rank)):
                return
            db.execute("INSERT OR REPLACE INTO observations VALUES(?,?,?,?,?)", (response_id, model, event, rank, time.time()))
            db.execute("UPDATE revision SET value=value+1 WHERE id=1")

    def revision(self):
        if not self.path.is_file():
            return 0
        try:
            with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.1) as db:
                row = db.execute("SELECT value FROM revision WHERE id=1").fetchone()
                return row[0] if row else 0
        except sqlite3.Error:
            return 0

    def lookup(self, ids):
        if not self.path.is_file():
            return {}
        values = list(dict.fromkeys(value for value in ids if isinstance(value, str) and value))
        result = {}
        try:
            with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.1) as db:
                for offset in range(0, len(values), 500):
                    chunk = values[offset:offset + 500]
                    result.update(db.execute("SELECT response_id,model FROM observations WHERE response_id IN (" + ",".join("?" for _ in chunk) + ")", chunk))
        except sqlite3.Error:
            pass
        return result


def enrich_rows(db, rows, store: UpstreamStore, *, grouped=False):
    if not rows or not store.path.is_file():
        return rows
    members = {row["id"]: [(row.get("response_id"), row.get("model"))] for row in rows}
    if grouped:
        members = {row["id"]: [] for row in rows}
        ids = list(members)
        for request_id, response_id, requested_model in db.execute(
                "SELECT m.request_id,json_extract(c.data,'$.response_id'),json_extract(c.data,'$.model') FROM usage_request_members m "
                "JOIN usage_priced_calls c ON c.id=m.record_id WHERE m.request_id IN (" + ",".join("?" for _ in ids) + ")", ids):
            members[request_id].append((response_id, requested_model))
    detected = store.lookup(response_id for values in members.values() for response_id, _ in values)
    for row in rows:
        calls = members[row["id"]]
        counts = Counter(detected[response_id] for response_id, _ in calls if response_id in detected)
        if counts:
            mismatches = sum(1 for response_id, requested in calls
                             if response_id in detected and requested and requested != detected[response_id])
            row.update(upstream_models=sorted(counts), upstream_model_counts=dict(counts),
                       upstream_detected_calls=sum(counts.values()),
                       upstream_mismatched_calls=mismatches,
                       upstream_total_calls=row.get("call_count", 1) if grouped else 1)
            if len(counts) == 1:
                row["upstream_model"] = next(iter(counts))
    return rows


class ResponseObserver:
    """Bounded SSE observer; failures never change forwarded response bytes."""
    MAX_EVENT = 2 * 1024 * 1024

    def __init__(self, emit, *, sse=True):
        self.emit = emit
        self.sse = sse
        self.buffer = bytearray()
        self.parts = []
        self.size = 0
        self.dropping = False

    def _observe(self, raw):
        try:
            value = json.loads(raw)
            kind = value.get("type", "response.completed")
            if self.sse and kind not in ("response.created", "response.completed", "response.incomplete", "response.failed"):
                return
            response = value.get("response", {}) if self.sse else value
            if isinstance(response, dict):
                self.emit(response.get("id"), response.get("model"), kind)
        except (ValueError, TypeError, AttributeError):
            pass

    def feed(self, chunk):
        if self.sse is None:
            if self.dropping:
                return
            if len(self.buffer) + len(chunk) > self.MAX_EVENT:
                self.buffer.clear()
                self.dropping = True
                return
            self.buffer.extend(chunk)
            probe = bytes(self.buffer).lstrip(b" \t\r\n")
            bom = b"\xef\xbb\xbf"
            if not probe or probe in (bom[:1], bom[:2]):
                return
            if probe.startswith(bom):
                probe = probe[len(bom):].lstrip(b" \t\r\n")
            if not probe:
                return
            self.sse = not probe.startswith((b"{", b"["))
            self.buffer.clear()
            chunk = probe
        if not self.sse:
            if len(self.buffer) + len(chunk) <= self.MAX_EVENT and not self.dropping:
                self.buffer.extend(chunk)
            else:
                self.buffer.clear()
                self.dropping = True
            return
        # Process one line at a time, with bounded memory even for a huge image event.
        for part in chunk.splitlines(keepends=True):
            self.size += len(part)
            if self.size > self.MAX_EVENT:
                self.dropping = True
                self.parts.clear()
                self.buffer.clear()
            if not self.dropping:
                self.buffer.extend(part)
            if part.endswith(b"\n"):
                line = bytes(self.buffer).rstrip(b"\r\n") if not self.dropping else None
                self.buffer.clear()
                if line == b"" or self.dropping and part in (b"\n", b"\r\n"):
                    if not self.dropping and self.parts:
                        self._observe(b"\n".join(self.parts))
                    self.parts.clear()
                    self.size = 0
                    self.dropping = False
                elif line is not None and line.startswith(b"data:"):
                    self.parts.append(line[5:].lstrip(b" "))

    def finish(self):
        if not self.sse and not self.dropping and self.buffer:
            self._observe(bytes(self.buffer))
