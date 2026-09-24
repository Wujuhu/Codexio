"""Bounded, account-scoped weekly allowance observations from local calls."""
from __future__ import annotations

import json
import math
import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

MATURITY_SECONDS = 120
MAX_ROWS = 100


def _stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="microseconds")


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


class RollingWeeklyEstimator:
    def __init__(self, path: Path, interval_minutes: int = 10):
        self.path = Path(path)
        self.interval_minutes = interval_minutes if interval_minutes in (10, 30, 60) else 10
        self.anchor = None
        self.checkpoint = None
        self.latest = None
        self.pending = deque(maxlen=180)
        self._price_version = None
        self._prepare()

    def _prepare(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("CREATE TABLE IF NOT EXISTS usage_week_intervals ("
                       "id INTEGER PRIMARY KEY AUTOINCREMENT,end_at TEXT NOT NULL,data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS week_intervals_end ON usage_week_intervals(end_at DESC,id DESC)")
            db.execute("DELETE FROM usage_week_intervals WHERE id NOT IN ("
                       "SELECT id FROM usage_week_intervals ORDER BY end_at DESC,id DESC LIMIT ?)", (MAX_ROWS,))
            migrated = db.execute("SELECT data FROM usage_meta WHERE key='rolling_week_migrated'").fetchone()
            if migrated:
                return
            for table in ("usage_records", "usage_record_sources", "usage_observations"):
                for action in ("insert", "update", "delete"):
                    db.execute("DROP TRIGGER IF EXISTS estimation_%s_%s" % (table, action))
            for table in ("usage_estimation_members", "usage_estimation_cycles",
                          "usage_estimation_observations", "usage_estimation_meta", "usage_estimation_changes"):
                db.execute("DROP TABLE IF EXISTS " + table)
            db.execute("DELETE FROM usage_observations")
            db.execute("DELETE FROM usage_origins WHERE kind='observation'")
            db.execute("INSERT OR REPLACE INTO usage_meta VALUES('rolling_week_migrated','1')")

    def configure(self, interval_minutes: int):
        interval = interval_minutes if interval_minutes in (10, 30, 60) else 10
        if interval != self.interval_minutes:
            self.interval_minutes = interval
            self.invalidate()

    def invalidate(self):
        self.anchor = self.checkpoint = self.latest = None
        self.pending.clear()

    @staticmethod
    def _identity(sample):
        return (sample["account_key"], sample["plan_type"], sample["limit_id"],
                sample["sole_codex_pool"])

    def add_sample(self, sample):
        stamp = _number(sample.get("timestamp"))
        used = _number(sample.get("used_percent"))
        reset = _number(sample.get("reset_at"))
        account = sample.get("account_key")
        plan = sample.get("plan_type")
        if (stamp is None or used is None or used > 100 or reset is None or reset <= stamp
                or not isinstance(account, str) or not account
                or not isinstance(plan, str) or not plan or plan in ("unknown", "legacy")
                or sample.get("limit_id") != "codex"):
            self.invalidate()
            return
        value = dict(timestamp=stamp, used_percent=used, reset_at=reset,
                     account_key=account, plan_type=plan, limit_id="codex",
                     sole_codex_pool=sample.get("sole_codex_pool") is True)
        previous = self.latest
        if previous is not None and (self._identity(value) != self._identity(previous)
                                     or abs(value["reset_at"] - previous["reset_at"]) > 60
                                     or stamp <= previous["timestamp"]
                                     or stamp - previous["timestamp"] > 600):
            self.invalidate()
        self.latest = value
        if self.anchor is None:
            self.anchor = self.checkpoint = value
        else:
            self.pending.append(value)

    def ready(self, now: float) -> bool:
        if self.checkpoint is None:
            return False
        return any(sample["timestamp"] <= now - MATURITY_SECONDS
                   and sample["timestamp"] - self.checkpoint["timestamp"] >= self.interval_minutes * 60
                   for sample in self.pending)

    def process_due(self, now: float, queries) -> bool:
        if not self.ready(now):
            return False
        candidates = [sample for sample in self.pending
                      if sample["timestamp"] <= now - MATURITY_SECONDS]
        sample = candidates[-1]
        while self.pending and self.pending[0]["timestamp"] <= sample["timestamp"]:
            self.pending.popleft()
        self.checkpoint = sample
        start = self.anchor
        delta = sample["used_percent"] - start["used_percent"]
        if delta < 0:
            self.anchor = sample
            return False
        if delta == 0:
            # No row is emitted. The next changed window includes this period's
            # priced calls and total tokens through the unchanged anchor.
            return False
        tokens, dollars, invalid = self._usage(
            queries, start["timestamp"], sample["timestamp"], start["sole_codex_pool"])
        self.anchor = sample
        if invalid or dollars <= 0:
            return False
        row = dict(start=_stamp(start["timestamp"]), end=_stamp(sample["timestamp"]),
                   plan_type=sample["plan_type"], account_key=sample["account_key"],
                   limit_id="codex", reset_at=sample["reset_at"],
                   sole_codex_pool=start["sole_codex_pool"],
                   start_percent=start["used_percent"], end_percent=sample["used_percent"],
                   delta_percent=delta, consumed_tokens=tokens, consumed_usd=dollars,
                   estimated_total_usd=100 * dollars / delta,
                   price_version=self._price_version)
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("INSERT INTO usage_week_intervals(end_at,data) VALUES(?,?)",
                       (row["end"], json.dumps(row, ensure_ascii=False, separators=(",", ":"))))
            db.execute("DELETE FROM usage_week_intervals WHERE id NOT IN ("
                       "SELECT id FROM usage_week_intervals ORDER BY end_at DESC,id DESC LIMIT ?)", (MAX_ROWS,))
        return True

    @staticmethod
    def _usage(queries, start, end, sole_codex_pool=False):
        tokens, costs, invalid = 0, [], False
        for record in queries.calibration_records({"local"}, start=_stamp(start),
                                                  end=_stamp(end), start_exclusive=True):
            if str(record.get("provider") or "").lower() not in ("openai", "codexio-upstream"):
                continue
            bucket = record.get("limit_id")
            if bucket != "codex" and not (sole_codex_pool and bucket in (None, "")):
                invalid = True
                continue
            cost = _number(record.get("cost_usd"))
            count = record.get("total_tokens")
            if (cost is None or record.get("pricing_status") not in ("priced", "estimated")
                    or not isinstance(count, int) or isinstance(count, bool) or count < 0):
                invalid = True
                continue
            tokens += count
            costs.append(cost)
        return tokens, math.fsum(costs), invalid

    def refresh_prices(self, price_version, queries):
        if price_version == self._price_version:
            return
        self._price_version = price_version
        with sqlite3.connect(self.path, timeout=30) as db:
            for ident, data in list(db.execute("SELECT id,data FROM usage_week_intervals")):
                row = json.loads(data)
                if row.get("price_version") == price_version:
                    continue
                start = datetime.fromisoformat(row["start"]).timestamp()
                end = datetime.fromisoformat(row["end"]).timestamp()
                tokens, dollars, invalid = self._usage(
                    queries, start, end, row.get("sole_codex_pool") is True)
                if invalid or dollars <= 0:
                    db.execute("DELETE FROM usage_week_intervals WHERE id=?", (ident,))
                    continue
                row.update(consumed_tokens=tokens, consumed_usd=dollars,
                           estimated_total_usd=100 * dollars / row["delta_percent"],
                           price_version=price_version)
                db.execute("UPDATE usage_week_intervals SET data=? WHERE id=?",
                           (json.dumps(row, ensure_ascii=False, separators=(",", ":")), ident))

    def rows(self):
        with sqlite3.connect(self.path, timeout=30) as db:
            return [json.loads(data) for (data,) in db.execute(
                "SELECT data FROM usage_week_intervals ORDER BY end_at DESC,id DESC LIMIT ?", (MAX_ROWS,))]
