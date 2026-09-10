"""Durable weekly-cycle cache with bounded, range-scoped ledger evaluation.

The existing estimator remains the numerical and status oracle. A cycle keeps
its exact observations and, when necessary, the following boundary observation
so early resets and plan changes retain their original meaning.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from codexio.estimation import (
    MATURITY_SECONDS, RESET_TOLERANCE_SECONDS, _account, _bucket, _finite, _time, estimate_weeks,
)

CACHE_VERSION = 3
OBSERVATION_FIELDS = ("timestamp", "resets_at", "used_percent", "window_minutes", "plan_type", "account_key",
                      "limit_id", "source_id", "pairing_delay_seconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class WeeklyEstimateCache:
    def __init__(self, queries):
        self.queries = queries
        self.stats = dict(evaluated_cycles=0, reused_cycles=0, rebuilt_timelines=0,
                          records_read=0, observations_read=0, evaluated_resets=[])

    @staticmethod
    def _schema(db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_estimation_observations (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, account TEXT, bucket TEXT NOT NULL,
                reset_at TEXT NOT NULL, plan TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS estimation_obs_timeline ON usage_estimation_observations(account,bucket,timestamp,id);
            CREATE INDEX IF NOT EXISTS estimation_obs_time ON usage_estimation_observations(timestamp);
            CREATE TABLE IF NOT EXISTS usage_estimation_cycles (
                id TEXT PRIMARY KEY, account TEXT, bucket TEXT NOT NULL,
                first_at TEXT NOT NULL, last_at TEXT NOT NULL, horizon TEXT NOT NULL,
                signature TEXT NOT NULL, data TEXT NOT NULL, evaluation_key TEXT NOT NULL,
                next_refresh TEXT NOT NULL, outputs TEXT NOT NULL, dirty INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS estimation_cycle_timeline ON usage_estimation_cycles(account,bucket,first_at);
            CREATE TABLE IF NOT EXISTS usage_estimation_members (
                cycle_id TEXT NOT NULL, observation_id TEXT NOT NULL, PRIMARY KEY(cycle_id,observation_id));
            CREATE TABLE IF NOT EXISTS usage_estimation_meta (key TEXT PRIMARY KEY,data TEXT NOT NULL);
        """)

    @staticmethod
    def _prepared(raw, active, since, assignments):
        if raw.get("source_id") not in active and raw.get("source_id") != "local-quota":
            return None
        stamp, reset = _time(raw.get("timestamp")), _time(raw.get("resets_at"))
        percent, minutes = _finite(raw.get("used_percent")), _finite(raw.get("window_minutes"))
        if not stamp or not reset or percent is None or not 0 <= percent <= 100 or minutes is None or abs(minutes - 10080) > 10:
            return None
        return dict(timestamp=_stamp(stamp), account=_account(raw, stamp, since, assignments), bucket=_bucket(raw),
                    reset_at=_stamp(reset), plan=_json(raw.get("plan_type")),
                    data=_json({key: raw[key] for key in OBSERVATION_FIELDS if key in raw}))

    @staticmethod
    def _insert_observation(db, ident, value):
        db.execute("INSERT INTO usage_estimation_observations VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "timestamp=excluded.timestamp,account=excluded.account,bucket=excluded.bucket,reset_at=excluded.reset_at,"
                   "plan=excluded.plan,data=excluded.data", (ident, value["timestamp"], value["account"], value["bucket"],
                                                          value["reset_at"], value["plan"], value["data"]))

    @staticmethod
    def _touch(affected, account, bucket, stamp):
        key = account, bucket
        if key not in affected or stamp < affected[key]:
            affected[key] = stamp

    def update(self, active, account_since, *, sources_complete=True, assignments=(), now=None):
        active = set(active)
        assignments = [row for row in (assignments or []) if isinstance(row, dict)]
        current = _time(now) or datetime.now(timezone.utc)
        current_text, since = _stamp(current), _time(account_since)
        prepare_key = _digest([CACHE_VERSION, sorted(active), account_since, assignments])
        outputs = []
        with self.queries._connect(write=True) as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            previous = dict(db.execute("SELECT key,data FROM usage_estimation_meta"))
            state = db.execute("SELECT data FROM usage_query_state WHERE key='signature'").fetchone()
            price_version = json.loads(state[0])[2] if state else None
            watermark = db.execute("SELECT data FROM usage_query_state WHERE key='materialized_change_seq'").fetchone()
            materialized_change_seq = int(watermark[0]) if watermark else 0
            evaluation_key = _digest([prepare_key, price_version, bool(sources_complete)])
            full_prepare = previous.get("prepare_key") != prepare_key
            last_now = previous.get("current_time", "")
            affected = {}
            if full_prepare:
                for table in ("usage_estimation_members", "usage_estimation_cycles", "usage_estimation_observations"):
                    db.execute("DELETE FROM " + table)
                for raw in db.execute("SELECT id,data FROM usage_observations ORDER BY timestamp,id"):
                    value = self._prepared(json.loads(raw[1]), active, since, assignments)
                    if value:
                        self._insert_observation(db, raw[0], value)
                        if value["timestamp"] <= current_text:
                            self._touch(affected, value["account"], value["bucket"], value["timestamp"])
            else:
                for event in db.execute("SELECT DISTINCT item_id FROM usage_estimation_changes WHERE kind='observation'"):
                    ident = event[0]
                    old = db.execute("SELECT account,bucket,timestamp FROM usage_estimation_observations WHERE id=?", (ident,)).fetchone()
                    if old and old[2] <= max(current_text, last_now):
                        self._touch(affected, old[0], old[1], old[2])
                    raw = db.execute("SELECT data FROM usage_observations WHERE id=?", (ident,)).fetchone()
                    value = self._prepared(json.loads(raw[0]), active, since, assignments) if raw else None
                    if value:
                        self._insert_observation(db, ident, value)
                        if value["timestamp"] <= current_text:
                            self._touch(affected, value["account"], value["bucket"], value["timestamp"])
                    else:
                        db.execute("DELETE FROM usage_estimation_observations WHERE id=?", (ident,))
                if last_now and last_now != current_text:
                    lower, upper = sorted((last_now, current_text))
                    for row in db.execute("SELECT account,bucket,MIN(timestamp) FROM usage_estimation_observations "
                                          "WHERE timestamp>? AND timestamp<=? GROUP BY account,bucket", (lower, upper)):
                        self._touch(affected, row[0], row[1], row[2])
                    if current_text < last_now:
                        db.execute("UPDATE usage_estimation_cycles SET dirty=1")
            for (account, bucket), earliest in affected.items():
                self._rebuild_tail(db, account, bucket, earliest, current_text)
            # A changed source alias or corrected old timestamp can affect a
            # closed cycle. Only intersecting cycles need their ledger reread.
            db.execute("UPDATE usage_estimation_cycles SET dirty=1 WHERE EXISTS(SELECT 1 FROM usage_estimation_changes e "
                       "WHERE e.kind='record' AND e.seq<=? AND e.timestamp>=usage_estimation_cycles.first_at AND e.timestamp<=usage_estimation_cycles.horizon)",
                       (materialized_change_seq,))
            for cycle in db.execute("SELECT * FROM usage_estimation_cycles ORDER BY first_at,json_extract(data,'$.first_observation')"):
                descriptor = json.loads(cycle["data"])
                due = bool(cycle["next_refresh"] and cycle["next_refresh"] <= current_text)
                if cycle["dirty"] or cycle["evaluation_key"] != evaluation_key or due:
                    values, next_refresh = self._evaluate(db, cycle, descriptor, active, account_since, assignments,
                                                          bool(sources_complete), current)
                    db.execute("UPDATE usage_estimation_cycles SET evaluation_key=?,next_refresh=?,outputs=?,dirty=0 WHERE id=?",
                               (evaluation_key, next_refresh, _json(values), cycle["id"]))
                else:
                    self.stats["reused_cycles"] += 1
                    values = json.loads(cycle["outputs"])
                outputs.extend(values)
            db.executemany("INSERT INTO usage_estimation_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                           [("prepare_key", prepare_key), ("current_time", current_text)])
            # Another collector may have written raw calls after the priced
            # snapshot committed. Keep those events until their priced rows are
            # visible, or their affected cycles would stay stale on the next tick.
            db.execute("DELETE FROM usage_estimation_changes WHERE kind='observation' OR (kind='record' AND seq<=?)",
                       (materialized_change_seq,))
        return sorted(outputs, key=lambda row: (row["reset_at"], row["end"]), reverse=True)

    def _rebuild_tail(self, db, account, bucket, earliest, current_text):
        self.stats["rebuilt_timelines"] += 1
        old = [dict(row) for row in db.execute("SELECT * FROM usage_estimation_cycles WHERE account IS ? AND bucket=? "
                                              "ORDER BY first_at,json_extract(data,'$.first_observation')", (account, bucket))]
        start = 0
        candidates = [index for index, row in enumerate(old) if row["first_at"] <= earliest]
        if candidates:
            start = candidates[-1]
            if earliest == old[start]["first_at"]:
                start = max(0, start - 1)
            while start and old[start - 1]["last_at"] >= old[start]["first_at"]:
                start -= 1
            earliest = min(earliest, old[start]["first_at"])
        tail = old[start:] if old else []
        previous = {row["id"]: row for row in tail}
        for row in tail:
            db.execute("DELETE FROM usage_estimation_members WHERE cycle_id=?", (row["id"],))
            db.execute("DELETE FROM usage_estimation_cycles WHERE id=?", (row["id"],))
        seen, group = set(), None
        for row in db.execute("SELECT * FROM usage_estimation_observations WHERE account IS ? AND bucket=? "
                              "AND timestamp>=? AND timestamp<=? ORDER BY timestamp,id", (account, bucket, earliest, current_text)):
            raw = json.loads(row["data"])
            identity = row["timestamp"], row["reset_at"], _finite(raw.get("used_percent")), row["plan"]
            if identity in seen:
                continue
            seen.add(identity)
            changed_plan = group and group["plan"] != row["plan"]
            changed_reset = group and abs((_time(group["reset_at"]) - _time(row["reset_at"])).total_seconds()) > RESET_TOLERANCE_SECONDS
            if group is None or changed_plan or changed_reset:
                if group:
                    group["next_observation"] = row["id"]
                    if changed_plan:
                        group["termination"] = "plan_changed"
                    elif _time(row["timestamp"]) < _time(group["reset_at"]) - timedelta(seconds=RESET_TOLERANCE_SECONDS):
                        group["termination"] = "early_reset"
                    self._save_cycle(db, group, previous)
                group = dict(id=_digest([account, bucket, row["id"]]), account=account, bucket=bucket,
                             first_at=row["timestamp"], last_at=row["timestamp"], reset_at=row["reset_at"], plan=row["plan"],
                             first_observation=row["id"], next_observation=None, termination=None, digest=hashlib.sha256())
            group["last_at"] = row["timestamp"]
            group["digest"].update(row["data"].encode("utf-8"))
            db.execute("INSERT INTO usage_estimation_members VALUES(?,?)", (group["id"], row["id"]))
        if group:
            self._save_cycle(db, group, previous)

    @staticmethod
    def _save_cycle(db, group, previous):
        descriptor = {key: value for key, value in group.items() if key != "digest"}
        signature = _digest([group["digest"].hexdigest(), group["termination"]])
        old = previous.get(group["id"])
        reuse = bool(old and old["signature"] == signature)
        # Terminated cycles depend on their observed intervals, not subsequent
        # activity before their superseded reset. Normal closed cycles also
        # depend on trailing records through reset for coverage classification.
        horizon = max(group["last_at"], group["reset_at"]) if not group["termination"] else group["last_at"]
        db.execute("INSERT INTO usage_estimation_cycles VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (group["id"], group["account"], group["bucket"], group["first_at"], group["last_at"], horizon,
                    signature, _json(descriptor), old["evaluation_key"] if reuse else "",
                    old["next_refresh"] if reuse else "", old["outputs"] if reuse else "[]", old["dirty"] if reuse else 1))

    def _evaluate(self, db, cycle, descriptor, active, account_since, assignments, sources_complete, current):
        observations, refreshes = [], []
        for row in db.execute("SELECT o.data,o.timestamp FROM usage_estimation_members m JOIN usage_estimation_observations o "
                              "ON o.id=m.observation_id WHERE m.cycle_id=? ORDER BY o.timestamp,o.id", (cycle["id"],)):
            observations.append(json.loads(row[0]))
            self.stats["observations_read"] += 1
            mature_at = _time(row[1]) + timedelta(seconds=MATURITY_SECONDS)
            if mature_at > current:
                refreshes.append(mature_at)
        if descriptor["termination"] and descriptor.get("next_observation"):
            following = db.execute("SELECT data FROM usage_estimation_observations WHERE id=?", (descriptor["next_observation"],)).fetchone()
            if following:
                observations.append(json.loads(following[0]))
                self.stats["observations_read"] += 1
        reset = _time(descriptor["reset_at"])
        if reset > current:
            refreshes.append(reset)

        def records():
            for row in self.queries.calibration_records(active, start=cycle["first_at"], end=cycle["horizon"]):
                self.stats["records_read"] += 1
                yield row

        evaluated = estimate_weeks(records(), observations, account_since, now=current,
                                   sources_complete=sources_complete, assignments=assignments)
        selected = [row for row in evaluated if row["reset_at"] == int(reset.timestamp())
                    and row["plan_type"] == json.loads(descriptor["plan"])
                    and row["account_key"] == descriptor["account"] and row["limit_id"] == descriptor["bucket"]]
        self.stats["evaluated_cycles"] += 1
        self.stats["evaluated_resets"].append(int(reset.timestamp()))
        return selected, _stamp(min(refreshes)) if refreshes else ""
