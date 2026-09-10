"""Bounded reads of an atomically rebuilt, priced presentation index.

The durable collector ledger remains authoritative. Only compact graph fields
are held during aggregation; full call details and completed groups stay in
SQLite, and a published UI snapshot contains no historical record lists.
"""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codexio.confirmed_usage import ConfirmedUsage, METRICS, _add_cost, summarize_confirmed_usage
from codexio.user_requests import COUNTERS, _merge_turns, iter_user_requests, normalized_tier, turn_key
from codexio.usage_collector import _user_preview

SCHEMA_VERSION = 4
METRIC_FIELDS = ("id", "timestamp", "model", "service_tier", "source_id", "source_name", "source_ids",
                 "session_id", "turn_id", "request_turn_id", "total_tokens", "cost_usd", "pricing_status",
                 "provider", "account_key", "limit_id", "quality", "duration_ms") + COUNTERS
TURN_FIELDS = ("id", "session_id", "turn_id", "verified", "observed_at", "ended_at", "status", "started_at",
               "started_inferred", "duration_ms", "first_turn", "input_hashes", "source_id", "source_ids", "alias_of",
               "is_subagent", "parent_session_id", "parent_turn_id", "agent_path", "has_usage", "synthetic")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _stamp(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def _count(value):
    # Match the legacy summary boundary: accept integral JSON floats and numeric
    # strings while keeping native integers exact, including values above 2**53.
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def summarize_model_calls(records):
    """One entry per model, summing actual call prices across tiers."""
    grouped = {}
    for record in records:
        model = str(record.get("model") or "未知模型")
        row = grouped.setdefault(model, dict(model=model, call_count=0, _tiers=set(), _costs=[], unpriced_calls=0, _estimated=False))
        row["call_count"] += 1
        row["_tiers"].add(normalized_tier(record.get("service_tier")))
        cost = record.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            _add_cost(row["_costs"], float(cost))
        else:
            row["unpriced_calls"] += 1
        row["_estimated"] |= record.get("pricing_status") == "estimated"
    for row in grouped.values():
        tiers, costs = row.pop("_tiers"), row.pop("_costs")
        estimated = row.pop("_estimated")
        known = row["call_count"] > row["unpriced_calls"]
        row["service_tier"] = next(iter(tiers)) if len(tiers) == 1 else "mixed"
        row["cost_usd"] = math.fsum(costs) if known else None
        row["pricing_status"] = ("unpriced" if not known else "partial" if row["unpriced_calls"] else "estimated" if estimated else "priced")
    return sorted(grouped.values(), key=lambda row: (-row["call_count"], row["model"]))


def comparison_bounds(period, now=None):
    days = {"today": 1, "week": 7, "month": 30}.get(period)
    if days is None:
        return None
    end = (now or datetime.now().astimezone()).astimezone()
    start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    shift = timedelta(days=days)
    return start, end, start - shift, start - timedelta(microseconds=1)


def compare_usage(records, period, now=None):
    """Compare the selected range with the preceding complete local-time unit."""
    bounds = comparison_bounds(period, now)
    if bounds is None:
        return None
    start, end, previous_start, previous_end = bounds
    ranges = ((_stamp(start), _stamp(end)), (_stamp(previous_start), _stamp(previous_end)))
    values = [ConfirmedUsage() for _ in ranges]
    for record in records:
        stamp = _stamp(record.get("timestamp"))
        if not stamp:
            continue
        for (lower, upper), value in zip(ranges, values):
            if not lower <= stamp <= upper:
                continue
            value.add(record)
    current, previous = (value.summary() for value in values)
    changes = {}
    for metric in METRICS:
        percent = None
        if current[metric] is None:
            status = "no_current"
        elif previous[metric] is None:
            status = "no_history"
        elif previous[metric] == 0 and current[metric] != 0:
            status = "zero_baseline"
        else:
            status = "ready"
            percent = (current[metric] - previous[metric]) / previous[metric] * 100 if previous[metric] else 0.0
        changes[metric] = dict(status=status, percent=percent)
    return dict(period=period, current=current, previous=previous, changes=changes,
                label={"today": "较昨天", "week": "较前 7 天", "month": "较前 30 天"}[period],
                start=start.isoformat(), end=end.isoformat(), previous_start=previous_start.isoformat(), previous_end=previous_end.isoformat())


class UsageQueries:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def _connect(self, *, write=False):
        db = sqlite3.connect(str(self.path) if write else self.path.as_uri() + "?mode=ro",
                             uri=not write, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if not write:
                db.execute("BEGIN")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _schema(db):
        db.executescript("""
            CREATE TABLE IF NOT EXISTS usage_priced_calls (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, model TEXT NOT NULL,
                tier TEXT NOT NULL, source_id TEXT NOT NULL, session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL, metrics TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS priced_calls_time ON usage_priced_calls(timestamp DESC,id DESC);
            CREATE INDEX IF NOT EXISTS priced_calls_model_time ON usage_priced_calls(model,timestamp);
            CREATE TABLE IF NOT EXISTS usage_query_sources (
                record_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(record_id,source_id));
            CREATE INDEX IF NOT EXISTS query_sources_source ON usage_query_sources(source_id,record_id);
            CREATE TABLE IF NOT EXISTS usage_request_groups (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, record_kind TEXT NOT NULL,
                is_subagent INTEGER NOT NULL, subagent_count INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS request_groups_time ON usage_request_groups(timestamp DESC,id DESC);
            CREATE TABLE IF NOT EXISTS usage_request_members (
                request_id TEXT NOT NULL, record_id TEXT NOT NULL, PRIMARY KEY(request_id,record_id));
            CREATE INDEX IF NOT EXISTS request_members_record ON usage_request_members(record_id,request_id);
            CREATE TABLE IF NOT EXISTS usage_query_turns (id TEXT PRIMARY KEY,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS usage_query_state (key TEXT PRIMARY KEY,data TEXT NOT NULL);
        """)

    def rebuild(self, catalog, sources=()):
        """Rebuild once per changed ledger/price/name revision and commit as a unit."""
        with self._connect(write=True) as db:
            self._schema(db)
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT revision FROM usage_revisions WHERE kind='ledger'").fetchone()[0]
            names = sorted((str(s.get("id") or s.get("source_id") or ""),
                            str(s.get("name") or s.get("source_name") or "")) for s in sources)
            signature = _json([SCHEMA_VERSION, revision, catalog.price_version, names])
            state = dict(db.execute("SELECT key,data FROM usage_query_state"))
            generation = int(state.get("generation", 0))
            materialized_change_seq = max(int(state.get("materialized_change_seq", 0)),
                                          db.execute("SELECT COALESCE(MAX(seq),0) FROM usage_estimation_changes").fetchone()[0])
            if state.get("signature") == signature:
                if "materialized_change_seq" not in state:
                    # Migration: matching raw revision proves this already-built
                    # index includes every current record event, even when an
                    # earlier app version did not persist a journal watermark.
                    db.execute("INSERT INTO usage_query_state VALUES('materialized_change_seq',?)", (str(materialized_change_seq),))
                return generation
            for table in ("usage_priced_calls", "usage_query_sources", "usage_request_groups",
                          "usage_request_members", "usage_query_turns"):
                db.execute("DELETE FROM " + table)
            db.execute("INSERT INTO usage_query_sources SELECT record_id,source_id FROM usage_record_sources")
            graph = []
            # Deduplicate frequent labels locally; unique IDs and detail text are
            # never interned into a process-lifetime global cache.
            labels = {}
            raw_sql = """SELECT r.data,t.title,
                (SELECT json_group_array(source_id) FROM
                    (SELECT source_id FROM usage_record_sources s WHERE s.record_id=r.id ORDER BY source_id))
                FROM usage_records r LEFT JOIN usage_session_titles t ON t.session_id=r.session_id
                ORDER BY r.timestamp DESC,r.id DESC"""

            def priced_rows():
                for raw in db.execute(raw_sql):
                    record = json.loads(raw[0])
                    if raw[1]:
                        record["session_title"] = raw[1]
                    record["source_ids"] = json.loads(raw[2]) or [record.get("source_id")]
                    pricing = catalog.price(record)
                    record.update(cost_usd=pricing.get("usd"), pricing_status=pricing.get("pricing_status", "unpriced"),
                                  pricing_reason=pricing.get("reason", ""), price_version=pricing.get("price_version"))
                    record.pop("price_rates", None)
                    metrics = {key: record[key] for key in METRIC_FIELDS if key in record}
                    for key in ("model", "service_tier", "source_id", "source_name", "quality", "provider", "limit_id"):
                        value = metrics.get(key)
                        if isinstance(value, str):
                            metrics[key] = labels.setdefault(value, value)
                    graph_row = dict(metrics)
                    # Presence is sufficient for choosing the primary preview.
                    # Its text is fetched only while writing this group's result.
                    graph_row["output_preview"] = bool(record.get("output_preview"))
                    graph.append(graph_row)
                    yield (record["id"], _stamp(record.get("timestamp")), str(record.get("model") or "未知模型"),
                           normalized_tier(record.get("service_tier")), str(record.get("source_id") or ""),
                           str(record.get("session_id") or ""), str(record.get("request_turn_id") or record.get("turn_id") or ""),
                           _json(metrics), _json(record))

            db.executemany("INSERT INTO usage_priced_calls VALUES(?,?,?,?,?,?,?,?,?)", priced_rows())
            # Merge duplicate/forked turn metadata in the same ordering as the
            # original aggregator, while keeping its preview strings on disk.
            for raw in db.execute("""SELECT data FROM usage_turns ORDER BY
                COALESCE(NULLIF(json_extract(data,'$.observed_at'),''),NULLIF(json_extract(data,'$.ended_at'),''),'') ASC,
                COALESCE(json_extract(data,'$.id'),'') ASC,id ASC"""):
                incoming = json.loads(raw[0])
                if not incoming.get("session_id") or not incoming.get("turn_id") or incoming.get("verified") is False:
                    continue
                key = turn_key(incoming["session_id"], incoming["turn_id"])
                previous = db.execute("SELECT data FROM usage_query_turns WHERE id=?", (key,)).fetchone()
                merged = _merge_turns(([json.loads(previous[0])] if previous else []) + [incoming])[key]
                db.execute("INSERT INTO usage_query_turns VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                           (key, _json(merged)))

            def slim_turns():
                for raw in db.execute("SELECT data FROM usage_query_turns ORDER BY id"):
                    full = json.loads(raw[0])
                    small = {key: full[key] for key in TURN_FIELDS if key in full}
                    small["prompt_preview"] = bool(full.get("prompt_preview"))
                    yield small

            links = (json.loads(row[0]) for row in db.execute("SELECT data FROM usage_agent_links ORDER BY id"))
            for group in iter_user_requests(graph, slim_turns(), links, sources, detail_keys=True):
                members = group.pop("member_ids")
                self._enrich_group(db, group)
                if group["record_kind"] == "unassigned":
                    group["member_ids"] = members[:1]
                db.execute("INSERT INTO usage_request_groups VALUES(?,?,?,?,?,?)",
                           (group["id"], _stamp(group.get("timestamp")), group["record_kind"],
                            int(group.get("is_subagent", False)), group.get("subagent_count", 0), _json(group)))
                db.executemany("INSERT INTO usage_request_members VALUES(?,?)", ((group["id"], ident) for ident in members))
            generation += 1
            db.executemany("INSERT INTO usage_query_state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                           [("signature", signature), ("generation", str(generation)),
                            ("materialized_change_seq", str(materialized_change_seq))])
            return generation

    @staticmethod
    def _enrich_group(db, group):
        def call(ident):
            row = db.execute("SELECT data FROM usage_priced_calls WHERE id=?", (ident,)).fetchone() if ident else None
            return json.loads(row[0]) if row else {}
        primary = call(group.pop("_primary_call_id", None))
        preview_call = call(group.pop("_preview_call_id", None))
        row = db.execute("SELECT data FROM usage_query_turns WHERE id=?", (group["id"],)).fetchone()
        meta = json.loads(row[0]) if row else {}
        preview = meta.get("output_preview") if meta.get("status") in ("completed", "aborted") else meta.get("latest_output_preview")
        group.update(session_title=primary.get("session_title") or meta.get("session_title") or "",
                     prompt_preview=_user_preview(meta.get("prompt_preview") or "") or _user_preview(primary.get("prompt_preview") or ""),
                     output_preview=preview or meta.get("output_preview") or preview_call.get("output_preview") or "")

    @staticmethod
    def _call_filters(alias, source, model, tier):
        clauses, args = [], []
        if source:
            clauses.append("EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=" + alias + ".id AND s.source_id=?)")
            args.append(source)
        if model:
            clauses.append(alias + ".model=?")
            args.append(model)
        if tier:
            clauses.append(alias + ".tier=?")
            args.append(normalized_tier(tier))
        return clauses, args

    @staticmethod
    def _dates(alias, start, end):
        clauses, args = [alias + ".timestamp<>''"], []
        for value, operator in ((start, ">="), (end, "<=")):
            if value is not None:
                clauses.append(alias + ".timestamp" + operator + "?")
                args.append(_stamp(value))
        return clauses, args

    @staticmethod
    def _pagination(page, page_size, total):
        size = max(1, min(100, int(page_size)))
        pages = max(1, (total + size - 1) // size)
        index = max(0, min(int(page), pages - 1))
        return index, size, pages

    def page(self, mode="user_request", start=None, end=None, source="", model="", tier="", page=0, page_size=100, search=""):
        grouped = mode != "model_call"
        alias, table = ("g", "usage_request_groups") if grouped else ("c", "usage_priced_calls")
        clauses, args = self._dates(alias, start, end)
        if str(search or "").strip():
            expressions = ["COALESCE(json_extract(" + alias + ".data,'$." + key + "'),'')"
                           for key in ("prompt_preview", "session_title", "session_id", "turn_id", "id", "model", "models")]
            clauses.append("instr(lower(" + " || ' ' || ".join(expressions) + "),lower(?))>0")
            args.append(str(search).strip())
        mixed = str(tier).lower() == "mixed"
        if mixed:
            clauses.append("json_extract(g.data,'$.service_tier')='mixed'" if grouped else "0=1")
        filters, values = self._call_filters("c", source, model, "" if mixed else tier)
        if filters:
            if grouped:
                # Find qualifying members once, then look up their whole groups.
                # A correlated EXISTS can rescan all calls for the chosen model
                # or source for every group, even with a forced join order.
                condition = "g.id IN(SELECT m.request_id FROM usage_priced_calls c JOIN usage_request_members m ON m.record_id=c.id WHERE " + " AND ".join(filters) + ")"
                # An as-yet unmetered request can match its observed source, but
                # it cannot satisfy model or service-tier member filters.
                if source and not model and not tier:
                    condition = "(" + condition + " OR (NOT EXISTS(SELECT 1 FROM usage_request_members m WHERE m.request_id=g.id) AND EXISTS(SELECT 1 FROM json_each(g.data,'$.source_ids') s WHERE s.value=?)))"
                    values.append(source)
                clauses.append(condition)
            else:
                clauses.extend(filters)
            args.extend(values)
        where = " WHERE " + " AND ".join(clauses)
        with self._connect() as db:
            if grouped:
                count = db.execute("SELECT COUNT(*),COALESCE(SUM(record_kind='user_request' AND is_subagent=0),0),"
                                   "COALESCE(SUM(record_kind='user_request' AND is_subagent<>0),0),COALESCE(SUM(record_kind='unassigned'),0) FROM "
                                   + table + " " + alias + where, args).fetchone()
                total = count[0]
                counts = dict(requests=count[1], subagents=count[2], unassigned=count[3])
            else:
                total = db.execute("SELECT COUNT(*) FROM " + table + " " + alias + where, args).fetchone()[0]
                counts = dict(requests=total, subagents=0, unassigned=0)
            index, size, pages = self._pagination(page, page_size, total)
            rows = [json.loads(row[0]) for row in db.execute("SELECT " + alias + ".data FROM " + table + " " + alias + where
                    + " ORDER BY " + alias + ".timestamp DESC," + alias + ".id DESC LIMIT ? OFFSET ?", args + [size, index * size])]
            return dict(rows=rows, total=total, page=index, pages=pages, counts=counts)

    def request_members(self, request_id, page=0, page_size=100):
        with self._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM usage_request_members WHERE request_id=?", (request_id,)).fetchone()[0]
            index, size, pages = self._pagination(page, page_size, total)
            rows = [json.loads(row[0]) for row in db.execute("SELECT c.data FROM usage_request_members m JOIN usage_priced_calls c ON c.id=m.record_id "
                    "WHERE m.request_id=? ORDER BY c.timestamp ASC,c.id ASC LIMIT ? OFFSET ?", (request_id, size, index * size))]
            return dict(rows=rows, total=total, page=index, pages=pages, counts=dict(requests=total, subagents=0, unassigned=0))

    def request_composition(self, request_id):
        with self._connect() as db:
            rows = db.execute("SELECT c.metrics FROM usage_request_members m JOIN usage_priced_calls c ON c.id=m.record_id "
                              "WHERE m.request_id=?", (request_id,))
            return summarize_model_calls(json.loads(row[0]) for row in rows)

    def _one(self, table, ident):
        with self._connect() as db:
            row = db.execute("SELECT data FROM " + table + " WHERE id=?", (ident,)).fetchone()
            return json.loads(row[0]) if row else None

    def record(self, ident):
        return self._one("usage_priced_calls", ident)

    def request(self, ident):
        return self._one("usage_request_groups", ident)

    def request_for_record(self, ident):
        with self._connect() as db:
            row = db.execute("SELECT g.data FROM usage_request_groups g JOIN usage_request_members m ON m.request_id=g.id WHERE m.record_id=? LIMIT 1", (ident,)).fetchone()
            return json.loads(row[0]) if row else None

    def latest_request(self):
        with self._connect() as db:
            row = db.execute("SELECT data FROM usage_request_groups WHERE record_kind='user_request' AND is_subagent=0 "
                             "ORDER BY timestamp DESC,id DESC LIMIT 1").fetchone()
            return json.loads(row[0]) if row else None

    def filters(self):
        with self._connect() as db:
            models = [row[0] for row in db.execute("SELECT DISTINCT model FROM usage_priced_calls ORDER BY model")]
            names = {}
            for row in db.execute("SELECT data FROM usage_sources ORDER BY id"):
                source = json.loads(row[0])
                ident = str(source.get("id") or source.get("source_id") or "")
                names[ident] = str(source.get("name") or source.get("source_name") or ident)
            sources = []
            for row in db.execute("SELECT s.source_id,MIN(CASE WHEN c.source_id=s.source_id THEN json_extract(c.data,'$.source_name') END) "
                                  "FROM usage_query_sources s JOIN usage_priced_calls c ON c.id=s.record_id GROUP BY s.source_id ORDER BY s.source_id"):
                sources.append(dict(id=row[0], name=names.get(row[0]) or row[1] or row[0]))
            return dict(models=models, sources=sources)

    def _metrics(self, start=None, end=None, model=""):
        clauses, args = self._dates("c", start, end)
        if model:
            clauses.append("c.model=?")
            args.append(model)
        with self._connect() as db:
            for row in db.execute("SELECT c.metrics FROM usage_priced_calls c WHERE " + " AND ".join(clauses)
                                  + " ORDER BY c.timestamp DESC,c.id DESC", args):
                yield json.loads(row[0])

    def chart_buckets(self, period="today", granularity="hour", start=None, end=None, model=""):
        from codexio.charts import bucket_records, period_bounds
        now = datetime.now().astimezone()
        lower, upper = period_bounds(period, now)
        lower, upper = start or lower, end or upper
        return bucket_records(self._metrics(lower, upper, model), period, granularity, now=now, start=lower, end=upper)

    def period_comparison(self, period, now=None, model=""):
        bounds = comparison_bounds(period, now)
        if bounds is None:
            return None
        _, end, previous_start, _ = bounds
        return compare_usage(self._metrics(previous_start, end, model), period, end)

    def confirmed_summary(self, now=None):
        """All-time overview cards; leave worker/calibration summaries intact."""
        return summarize_confirmed_usage(self._metrics(end=now or datetime.now().astimezone()))

    def summaries(self, now=None):
        local = (now or datetime.now().astimezone()).astimezone()
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        starts = {"today": midnight, "week": midnight - timedelta(days=6), "month": midnight - timedelta(days=29), "all": None}
        output = {key: dict(tokens=0, requests=0, unpriced_requests=0, unpriced_tokens=0, input_tokens=0,
                            cached_input_tokens=0, _rows=0, _known=0, _partials=[]) for key in starts}
        bounds = {key: _stamp(value) if value else "" for key, value in starts.items()}
        for record in self._metrics(end=local):
            stamp = _stamp(record.get("timestamp"))
            cost = record.get("cost_usd")
            known = isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0
            for key, lower in bounds.items():
                if lower and stamp < lower:
                    continue
                row = output[key]
                row["_rows"] += 1
                row["tokens"] += _count(record.get("total_tokens"))
                row["input_tokens"] += _count(record.get("input_tokens"))
                row["cached_input_tokens"] += _count(record.get("cached_input_tokens"))
                row["requests"] += str(record.get("quality", "")).split(":", 1)[0] not in ("aggregate", "cumulative", "cumulative_observation", "unresolved")
                if known:
                    row["_known"] += 1
                    _add_cost(row["_partials"], float(cost))
                else:
                    row["unpriced_requests"] += 1
                    row["unpriced_tokens"] += _count(record.get("total_tokens"))
        for row in output.values():
            count, known, partials = row.pop("_rows"), row.pop("_known"), row.pop("_partials")
            row["usd"] = math.fsum(partials) if known or not count else None
            row["cost_status"] = "unpriced" if count and not known else "partial" if row["unpriced_requests"] else "priced"
            row["cache_hit_rate"] = row["cached_input_tokens"] / row["input_tokens"] if row["input_tokens"] else 0
        return output

    def next_record_at(self, after):
        """Next future-dated call that can change summaries without a write."""
        with self._connect() as db:
            return db.execute("SELECT MIN(timestamp) FROM usage_priced_calls WHERE timestamp>?", (_stamp(after),)).fetchone()[0]

    def history_source_options(self):
        available = {source["id"]: source["name"] for source in self.filters()["sources"]}
        with self._connect() as db:
            return [dict(id=row[0], name=available.get(row[0]) or row[0], start=row[1], end=row[2], count=row[3])
                    for row in db.execute("SELECT c.source_id,MIN(c.timestamp),MAX(c.timestamp),COUNT(*) FROM usage_priced_calls c GROUP BY c.source_id ORDER BY c.source_id")]

    def calibration_records(self, active, start=None, end=None):
        active = sorted(active)
        if not active:
            return
        placeholders = ",".join("?" for _ in active)
        dates, date_args = self._dates("c", start, end)
        # Scoped cycle reads must start from their time range. Otherwise SQLite
        # can infer an ID list from the active sources and reread their entire
        # history once for every small calibration cycle.
        time_index = " INDEXED BY priced_calls_time" if start is not None or end is not None else ""
        with self._connect() as db:
            sql = ("SELECT c.metrics,(SELECT MIN(s.source_id) FROM usage_query_sources s WHERE s.record_id=c.id AND s.source_id IN ("
                   + placeholders + ")) AS active_origin FROM usage_priced_calls c" + time_index + " WHERE EXISTS(SELECT 1 FROM usage_query_sources s "
                   "WHERE s.record_id=c.id AND s.source_id IN (" + placeholders + ")) AND " + " AND ".join(dates))
            for row in db.execute(sql, active + active + date_args):
                record = json.loads(row[0])
                if record.get("source_id") not in active:
                    record["source_id"] = row[1]
                yield record

    def weekly_estimates(self, active, account_since, *, sources_complete=True, assignments=(), now=None):
        from codexio.estimation_cache import WeeklyEstimateCache
        cache = WeeklyEstimateCache(self)
        result = cache.update(active, account_since, sources_complete=sources_complete, assignments=assignments, now=now)
        self.last_estimation_stats = cache.stats
        return result

    def calibration_observations(self, active):
        sources = sorted(set(active) | {"local-quota"})
        with self._connect() as db:
            sql = ("SELECT data FROM usage_observations WHERE json_extract(data,'$.source_id') IN ("
                   + ",".join("?" for _ in sources) + ") AND ABS(CAST(json_extract(data,'$.window_minutes') AS REAL)-10080)<=10 ORDER BY timestamp,id")
            for row in db.execute(sql, sources):
                yield json.loads(row[0])
