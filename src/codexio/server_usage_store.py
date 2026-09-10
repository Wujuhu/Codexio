"""Small, independent server ledger; local rescans never erase quota history."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ServerUsageStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
                CREATE TABLE IF NOT EXISTS quota (account TEXT, timestamp TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(account,timestamp));
                CREATE TABLE IF NOT EXISTS daily (account TEXT, day TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(account,day));
                CREATE TABLE IF NOT EXISTS context (account TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS active (id INTEGER PRIMARY KEY CHECK(id=1), account TEXT, status TEXT);
            """)

    def save(self, context, *, observation=None, daily=None, date_range=None):
        key = context.get("account_key")
        with closing(sqlite3.connect(self.path, timeout=10)) as con, con:
            con.execute("INSERT OR REPLACE INTO active VALUES(1,?,?)", (key, context.get("quota_status", "collecting")))
            if not key:
                return
            con.execute("INSERT OR REPLACE INTO context VALUES(?,?)", (key, json.dumps(context)))
            if observation:
                if observation["account_key"] != key:
                    raise ValueError("Account mismatch")
                con.execute("INSERT OR REPLACE INTO quota VALUES(?,?,?)", (key, observation["timestamp"], json.dumps(observation)))
            if daily is not None:
                if any(row["account_key"] != key for row in daily):
                    raise ValueError("Account mismatch")
                # A revised fetch can remove a previously returned row. Missing
                # rows remain missing, rather than keeping a stale zero or cost.
                if date_range:
                    con.execute("DELETE FROM daily WHERE account=? AND day>=? AND day<=?", (key, *date_range))
                con.executemany("INSERT OR REPLACE INTO daily VALUES(?,?,?)",
                                [(key, r["date"], json.dumps(r)) for r in daily])
            cutoff = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
            con.execute("DELETE FROM quota WHERE timestamp<?", (cutoff,))
            con.execute("DELETE FROM daily WHERE day<?", (cutoff[:10],))

    def account_context(self, key):
        with closing(sqlite3.connect(self.path, timeout=10)) as con:
            row = con.execute("SELECT payload FROM context WHERE account=?", (key,)).fetchone()
            return json.loads(row[0]) if row else {"account_key": key}


def read_server_snapshot(path):
    path = Path(path)
    if not path.exists():
        return dict(context={"quota_status": "collecting"}, observations=[], daily=[])
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as con:
            con.execute("BEGIN")  # One consistent read during a background refresh.
            active = con.execute("SELECT account,status FROM active WHERE id=1").fetchone()
            key, status = active if active else (None, "collecting")
            context_row = con.execute("SELECT payload FROM context WHERE account=?", (key,)).fetchone()
            context = json.loads(context_row[0]) if context_row else {"account_key": key, "quota_status": status}
            observations = [json.loads(row[0]) for row in con.execute("SELECT payload FROM quota WHERE account=? ORDER BY timestamp", (key,))]
            daily = [json.loads(row[0]) for row in con.execute("SELECT payload FROM daily WHERE account=? ORDER BY day", (key,))]
            return dict(context=context, observations=observations, daily=daily)
    except (OSError, sqlite3.Error, ValueError):
        return dict(context={"quota_status": "storage_error"}, observations=[], daily=[])
