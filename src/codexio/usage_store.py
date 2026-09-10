"""Local usage index with authorized short user/assistant-message previews.

Full conversations and credentials are never stored.

Connections are deliberately short lived: the background collector and Qt UI may
use the same store safely, without sharing a sqlite connection across threads.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def utc_iso(value: Any = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    elif isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class UsageStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS usage_records (
                    id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                    model TEXT NOT NULL, session_id TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_records_time ON usage_records(timestamp DESC);
                CREATE INDEX IF NOT EXISTS usage_records_model_time ON usage_records(model,timestamp);
                CREATE TABLE IF NOT EXISTS usage_record_sources (
                    record_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    PRIMARY KEY(record_id, source_id)
                );
                CREATE INDEX IF NOT EXISTS usage_sources_lookup ON usage_record_sources(source_id,record_id);
                CREATE TABLE IF NOT EXISTS usage_observations (
                    id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_obs_time ON usage_observations(timestamp);
                CREATE TABLE IF NOT EXISTS usage_sources (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_cursors (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_meta (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_session_titles (session_id TEXT PRIMARY KEY, title TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_turns (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_agent_links (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS usage_origins (
                    source_id TEXT NOT NULL, file_key TEXT NOT NULL, kind TEXT NOT NULL,
                    item_id TEXT NOT NULL, generation TEXT NOT NULL,
                    PRIMARY KEY(source_id,file_key,kind,item_id)
                );
                CREATE INDEX IF NOT EXISTS usage_origins_item ON usage_origins(source_id,kind,item_id);
                CREATE TABLE IF NOT EXISTS usage_revisions (kind TEXT PRIMARY KEY, revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO usage_revisions VALUES('ledger',0),('observations',0);
                CREATE TABLE IF NOT EXISTS usage_estimation_changes (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    item_id TEXT NOT NULL, timestamp TEXT);
                CREATE INDEX IF NOT EXISTS estimation_changes_kind_time ON usage_estimation_changes(kind,timestamp);
            """)
            # Changes to durable inputs invalidate the derived query index. Cursor
            # checkpoints and repeated source health updates do not reprice history.
            for table in ("usage_records", "usage_record_sources", "usage_session_titles", "usage_turns", "usage_agent_links", "usage_observations"):
                kind = "observations" if table == "usage_observations" else "ledger"
                for action in ("INSERT", "UPDATE", "DELETE"):
                    db.execute("CREATE TRIGGER IF NOT EXISTS revision_%s_%s AFTER %s ON %s BEGIN "
                               "UPDATE usage_revisions SET revision=revision+1 WHERE kind='%s'; END"
                               % (table, action.lower(), action, table, kind))
            # The cycle estimator consumes and prunes this journal after an
            # atomic update. Unlike a global revision, it identifies old and new
            # time ranges for late counters, deletes and source-alias changes.
            for table in ("usage_records", "usage_record_sources", "usage_observations"):
                for action, versions in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",))):
                    statements = []
                    for version in versions:
                        if table == "usage_observations":
                            kind, identity, stamp = "observation", version + ".id", "NULL"
                        else:
                            kind = "record"
                            identity = version + (".record_id" if table == "usage_record_sources" else ".id")
                            raw_stamp = ("(SELECT timestamp FROM usage_records WHERE id=" + identity + ")"
                                         if table == "usage_record_sources" else version + ".timestamp")
                            stamp = "strftime('%Y-%m-%dT%H:%M:%f'," + raw_stamp + ")||'000+00:00'"
                        statements.append("INSERT INTO usage_estimation_changes(kind,item_id,timestamp) VALUES('%s',%s,%s);"
                                          % (kind, identity, stamp))
                    db.execute("CREATE TRIGGER IF NOT EXISTS estimation_%s_%s AFTER %s ON %s BEGIN %s END"
                               % (table, action.lower(), action, table, " ".join(statements)))

    def revisions(self) -> dict:
        with self._connect() as db:
            return dict(db.execute("SELECT kind,revision FROM usage_revisions"))

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    def _upsert_records(self, db, records: Iterable[dict], source_id: str) -> int:
        changed = 0
        for incoming in records:
            record = dict(incoming)
            origin = record.pop("_origin", None)
            # A response retains its identity when a rollout is forked, copied,
            # archived, or collected from a second machine.
            if record.get("response_id"):
                record["id"] = "response:{}:{}".format(record.get("session_id", ""), record["response_id"])
            record["source_id"] = source_id
            record["timestamp"] = utc_iso(record["timestamp"])
            record["session_title"] = str(record.get("session_title") or "")[:400]
            record["prompt_preview"] = str(record.get("prompt_preview") or "")[:600]
            record["output_preview"] = str(record.get("output_preview") or "")[:600]
            if record.get("context_owner_verified") is False:
                record["prompt_preview"] = record["output_preview"] = ""
            # Pricing belongs to the pricing engine, never this durable index.
            for field in list(record):
                if "cost" in field or field in {"price", "usd"}:
                    record.pop(field)
            existing = db.execute("SELECT data FROM usage_records WHERE id=?", (record["id"],)).fetchone()
            if existing:
                old = json.loads(existing[0])
                # Response IDs are immutable upstream identities. Copies can
                # carry child context or another observation timestamp; do not
                # overwrite the original response's ownership with that context.
                other_origin = bool(origin and db.execute("SELECT 1 FROM usage_origins WHERE source_id=? AND kind='record' AND item_id=? AND file_key<>? LIMIT 1",
                                                          (source_id, record["id"], origin.get("file_key"))).fetchone())
                better_context = record.get("context_owner_verified") and not old.get("context_owner_verified")
                copied = not better_context and (old.get("source_id") != source_id or other_origin or old.get("context_owner_verified") and not record.get("context_owner_verified"))
                if record.get("response_id") and copied:
                    for field in ("timestamp", "model", "service_tier", "turn_id", "request_turn_id", "session_id", "prompt_preview", "output_preview", "limit_id", "duration_ms", "call_started_at", "call_ended_at"):
                        if old.get(field) not in (None, "", "unknown"):
                            record[field] = old[field]
                old_is_response = str(old.get("quality", "")).startswith("response")
                new_is_response = str(record.get("quality", "")).startswith("response")
                if record.get("response_id") and old_is_response:
                    # Prefer modern data to legacy enrichment, and refuse to
                    # rewrite immutable counters from conflicting copied logs.
                    counters = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
                    if not new_is_response or any(record.get(k) != old.get(k) for k in counters):
                        for field in counters + ("reported_total_tokens", "quality", "timestamp"):
                            if field in old:
                                record[field] = old[field]
                # Do not oscillate the displayed origin during cross-host sync.
                for field in ("source_id", "source_name"):
                    if old.get(field):
                        record[field] = old[field]
                for field in ("session_title", "prompt_preview", "output_preview"):
                    if not record.get(field):
                        record[field] = old.get(field, "")
                for field in ("model", "service_tier", "limit_id", "provider"):
                    if record.get(field) in (None, "", "unknown") and old.get(field) not in (None, "", "unknown"):
                        record[field] = old[field]
                for field in ("duration_ms", "call_started_at", "call_ended_at"):
                    if record.get(field) is None and old.get(field) is not None:
                        record[field] = old[field]
                if old.get("context_owner_verified"):
                    record["context_owner_verified"] = True
            payload = self._json(record)
            if not existing or existing[0] != payload:
                db.execute("""INSERT INTO usage_records(id,timestamp,model,session_id,data) VALUES(?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp, model=excluded.model,
                    session_id=excluded.session_id,data=excluded.data""",
                    (record["id"], record["timestamp"], record.get("model") or "unknown", record.get("session_id") or "", payload))
                changed += 1
            linked = db.execute("INSERT OR IGNORE INTO usage_record_sources VALUES(?,?)", (record["id"], source_id)).rowcount
            if existing and linked:
                changed += 1
            self._save_origin(db, origin, source_id, "record", record["id"])
        return changed

    def _upsert_observations(self, db, observations: Iterable[dict], source_id: str) -> int:
        changed = 0
        for incoming in observations:
            observation = dict(incoming)
            origin = observation.pop("_origin", None)
            observation["source_id"] = source_id
            observation["timestamp"] = utc_iso(observation["timestamp"])
            identity = source_id + "|" + observation["id"]
            payload = self._json(observation)
            old = db.execute("SELECT data FROM usage_observations WHERE id=?", (identity,)).fetchone()
            if not old or old[0] != payload:
                db.execute("INSERT INTO usage_observations VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET timestamp=excluded.timestamp,data=excluded.data",
                    (identity, observation["timestamp"], payload))
                changed += 1
            self._save_origin(db, origin, source_id, "observation", identity)
        return changed

    @staticmethod
    def _save_origin(db, origin, source_id, kind, identity):
        if isinstance(origin, dict) and origin.get("file_key") and origin.get("generation"):
            db.execute("""INSERT INTO usage_origins VALUES(?,?,?,?,?)
                ON CONFLICT(source_id,file_key,kind,item_id) DO UPDATE SET generation=excluded.generation""",
                (source_id, str(origin["file_key"]), kind, identity, str(origin["generation"])))

    def upsert_records(self, records, source_id: str) -> int:
        with self._connect() as db:
            return self._upsert_records(db, records, source_id)

    def upsert_observations(self, observations, source_id: str) -> int:
        with self._connect() as db:
            return self._upsert_observations(db, observations, source_id)

    def _upsert_metadata(self, db, rows, source_id: str, kind: str) -> int:
        table = {"turn": "usage_turns", "agent_link": "usage_agent_links"}[kind]
        changed = 0
        for incoming in rows:
            value = dict(incoming)
            origin = value.pop("_origin", None)
            value["source_id"] = source_id
            value["prompt_preview"] = str(value.get("prompt_preview") or "")[:600]
            value["output_preview"] = str(value.get("output_preview") or "")[:600]
            value["latest_output_preview"] = str(value.get("latest_output_preview") or "")[:600]
            file_key = str((origin or {}).get("file_key") or "manual")
            identity = source_id + "|" + file_key + "|" + str(value["id"])
            value["_generation"] = (origin or {}).get("generation")
            existing = db.execute("SELECT data FROM " + table + " WHERE id=?", (identity,)).fetchone()
            if existing:
                old = json.loads(existing[0])
                if old.get("_generation") == value.get("_generation"):
                    for field in ("prompt_preview", "output_preview", "latest_output_preview", "started_at", "ended_at"):
                        if not value.get(field) and old.get(field):
                            value[field] = old[field]
                    if value.get("started_inferred") and old.get("started_at") and not old.get("started_inferred"):
                        value["started_at"], value["started_inferred"] = old["started_at"], False
                    value["input_hashes"] = list(dict.fromkeys(old.get("input_hashes", []) + value.get("input_hashes", [])))[-32:]
                    if value.get("status") == "unknown" and old.get("ended_at"):
                        value["status"] = old.get("status", "unknown")
                    value["first_turn"] = bool(old.get("first_turn") or value.get("first_turn"))
            payload = self._json(value)
            if not existing or existing[0] != payload:
                db.execute("INSERT INTO " + table + " VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (identity, payload))
                changed += 1
            self._save_origin(db, origin, source_id, kind, identity)
        return changed

    def upsert_turns(self, rows, source_id="local") -> int:
        with self._connect() as db:
            return self._upsert_metadata(db, rows, source_id, "turn")

    def upsert_agent_links(self, rows, source_id="local") -> int:
        with self._connect() as db:
            return self._upsert_metadata(db, rows, source_id, "agent_link")

    def turns(self) -> list:
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM usage_turns ORDER BY id")]

    def agent_links(self) -> list:
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM usage_agent_links ORDER BY id")]

    def record_sources(self) -> dict:
        result = {}
        with self._connect() as db:
            for row in db.execute("SELECT record_id,source_id FROM usage_record_sources ORDER BY source_id"):
                result.setdefault(row[0], []).append(row[1])
        return result

    def commit_batch(self, records, observations, source_id: str, key: str, cursor: dict, *, turns=(), agent_links=()) -> int:
        with self._connect() as db:
            changed = self._upsert_records(db, records, source_id)
            changed += self._upsert_observations(db, observations, source_id)
            changed += self._upsert_metadata(db, turns, source_id, "turn")
            changed += self._upsert_metadata(db, agent_links, source_id, "agent_link")
            if cursor.get("scan_complete") and cursor.get("reconcile_pending"):
                changed += self._reconcile_origin(db, source_id, key, cursor["generation"])
                cursor = dict(cursor, reconcile_pending=False)
            db.execute("INSERT INTO usage_cursors VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (key, self._json(cursor)))
            return changed

    def import_frames(self, frames: dict, source_id: str, meta_key: Optional[str] = None) -> int:
        """Atomically import a remote pass, reconcile rewrites, and save its cursor."""
        with self._connect() as db:
            changed = self._upsert_records(db, frames.get("records", []), source_id)
            changed += self._upsert_observations(db, frames.get("observations", []), source_id)
            changed += self._upsert_metadata(db, frames.get("turns", []), source_id, "turn")
            changed += self._upsert_metadata(db, frames.get("agent_links", []), source_id, "agent_link")
            for origin in frames.get("reconciliations", []):
                changed += self._reconcile_origin(db, source_id, origin["file_key"], origin["generation"])
            if meta_key is not None:
                db.execute("INSERT INTO usage_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                           (meta_key, self._json(frames.get("cursors", {}))))
            return changed

    def _reconcile_origin(self, db, source_id, file_key, generation):
        stale = db.execute("SELECT kind,item_id FROM usage_origins WHERE source_id=? AND file_key=? AND generation<>?",
                           (source_id, file_key, generation)).fetchall()
        db.execute("DELETE FROM usage_origins WHERE source_id=? AND file_key=? AND generation<>?", (source_id, file_key, generation))
        changed = 0
        for kind, identity in stale:
            still_present = db.execute("SELECT 1 FROM usage_origins WHERE source_id=? AND kind=? AND item_id=? LIMIT 1",
                                       (source_id, kind, identity)).fetchone()
            if still_present:
                continue
            if kind == "record":
                changed += self._delete_source_records(db, [identity], source_id)
            elif kind in ("turn", "agent_link"):
                table = {"turn": "usage_turns", "agent_link": "usage_agent_links"}[kind]
                changed += db.execute("DELETE FROM " + table + " WHERE id=?", (identity,)).rowcount
            else:
                row = db.execute("SELECT data FROM usage_observations WHERE id=?", (identity,)).fetchone()
                if row:
                    db.execute("DELETE FROM usage_observations WHERE id=?", (identity,))
                    changed += 1
        return changed

    def _delete_source_records(self, db, ids, source_id):
        changed = 0
        for identity in set(ids):
            removed = db.execute("DELETE FROM usage_record_sources WHERE source_id=? AND record_id=?", (source_id, identity)).rowcount
            if not removed:
                continue
            changed += 1
            db.execute("DELETE FROM usage_origins WHERE source_id=? AND kind='record' AND item_id=?", (source_id, identity))
            remaining = db.execute("SELECT source_id FROM usage_record_sources WHERE record_id=? ORDER BY source_id LIMIT 1", (identity,)).fetchone()
            if remaining:
                row = db.execute("SELECT data FROM usage_records WHERE id=?", (identity,)).fetchone()
                if row:
                    data = json.loads(row[0])
                    if data.get("source_id") == source_id:
                        data["source_id"] = remaining[0]
                        source = db.execute("SELECT data FROM usage_sources WHERE id=?", (remaining[0],)).fetchone()
                        details = json.loads(source[0]) if source else {}
                        data["source_name"] = details.get("name") or details.get("source_name") or remaining[0]
                        db.execute("UPDATE usage_records SET data=? WHERE id=?", (self._json(data), identity))
            else:
                db.execute("DELETE FROM usage_records WHERE id=?", (identity,))
        return changed

    def delete_source_records(self, ids, source_id: str) -> int:
        with self._connect() as db:
            return self._delete_source_records(db, ids, source_id)

    def delete_source_observations(self, ids, source_id: str) -> int:
        with self._connect() as db:
            changed = 0
            for identity in set(ids):
                key = source_id + "|" + identity
                changed += db.execute("DELETE FROM usage_observations WHERE id=?", (key,)).rowcount
                db.execute("DELETE FROM usage_origins WHERE source_id=? AND kind='observation' AND item_id=?", (source_id, key))
            return changed

    @staticmethod
    def _filters(start=None, end=None, source_id=None, model=None):
        conditions, values = [], []
        if start is not None:
            conditions.append("r.timestamp>=?")
            values.append(utc_iso(start))
        if end is not None:
            conditions.append("r.timestamp<?")
            values.append(utc_iso(end))
        if source_id:
            conditions.append("EXISTS(SELECT 1 FROM usage_record_sources s WHERE s.record_id=r.id AND s.source_id=?)")
            values.append(source_id)
        if model:
            conditions.append("r.model=?")
            values.append(model)
        return (" WHERE " + " AND ".join(conditions) if conditions else ""), values

    def records(self, start=None, end=None, source_id=None, model=None, limit=None, offset=0) -> list:
        where, args = self._filters(start, end, source_id, model)
        sql = "SELECT r.data,t.title FROM usage_records r LEFT JOIN usage_session_titles t ON t.session_id=r.session_id" + where
        sql += " ORDER BY r.timestamp DESC,r.id DESC LIMIT ? OFFSET ?"
        args += [-1 if limit is None else max(0, int(limit)), max(0, int(offset))]
        with self._connect() as db:
            result = []
            for row in db.execute(sql, args):
                record = json.loads(row[0])
                if row[1]:
                    record["session_title"] = row[1]
                result.append(record)
            return result

    def count_records(self, start=None, end=None, source_id=None, model=None, **_ignored) -> int:
        where, args = self._filters(start, end, source_id, model)
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM usage_records r" + where, args).fetchone()[0]

    def observations(self) -> list:
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM usage_observations ORDER BY timestamp,id")]

    def sources(self) -> list:
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM usage_sources ORDER BY id")]

    def set_source_status(self, source_id: str, **fields) -> None:
        with self._connect() as db:
            row = db.execute("SELECT data FROM usage_sources WHERE id=?", (source_id,)).fetchone()
            value = json.loads(row[0]) if row else {"id": source_id, "source_id": source_id}
            value.update(fields)
            db.execute("INSERT INTO usage_sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (source_id, self._json(value)))

    def models(self) -> list:
        with self._connect() as db:
            return [row[0] for row in db.execute("SELECT DISTINCT model FROM usage_records ORDER BY model")]

    def get_cursor(self, key: str) -> Optional[dict]:
        return self._get_value("usage_cursors", key)

    def set_cursor(self, key: str, state: dict) -> None:
        self._set_value("usage_cursors", key, state)

    def get_meta(self, key: str) -> Any:
        return self._get_value("usage_meta", key)

    def set_meta(self, key: str, value: Any) -> None:
        self._set_value("usage_meta", key, value)

    def _get_value(self, table, key):
        with self._connect() as db:
            row = db.execute("SELECT data FROM " + table + " WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def _set_value(self, table, key, value):
        with self._connect() as db:
            db.execute("INSERT INTO " + table + " VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (key, self._json(value)))

    def update_session_titles(self, titles: dict) -> int:
        changed = 0
        with self._connect() as db:
            for session_id, title in titles.items():
                title = str(title)[:400]
                old = db.execute("SELECT title FROM usage_session_titles WHERE session_id=?", (session_id,)).fetchone()
                if title and (not old or old[0] != title):
                    db.execute("INSERT INTO usage_session_titles VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET title=excluded.title", (session_id, title))
                    changed += 1
        return changed

    def clear_index(self) -> None:
        with self._connect() as db:
            for table in ("usage_records", "usage_record_sources", "usage_observations", "usage_turns", "usage_agent_links", "usage_cursors", "usage_session_titles", "usage_origins"):
                db.execute("DELETE FROM " + table)
            # Remote cursors must be reset too, otherwise clearing local data
            # would permanently skip the already-consumed remote prefix.
            db.execute("DELETE FROM usage_meta WHERE key LIKE '%cursor%'")

