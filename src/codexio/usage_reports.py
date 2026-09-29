"""Closed local-calendar AI reports over the existing priced SQLite index."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from codexio.confirmed_usage import confirmed_call, confirmed_cost, confirmed_count, confirmed_tokens
from codexio.i18n import tr
from codexio.settings import data_dir

PERIODS = ("day", "week", "month")


def preferred_period(today: date) -> str:
    return "month" if today.day == 1 else "week" if today.weekday() == 0 else "day"


def _previous_month(first: date) -> date:
    return (first - timedelta(days=1)).replace(day=1)


def ranges(today: date) -> dict[str, tuple[date, date, date]]:
    monday = today - timedelta(days=today.weekday())
    first = today.replace(day=1)
    month_start = _previous_month(first)
    return {
        "day": (today-timedelta(days=1), today, today-timedelta(days=2)),
        "week": (monday-timedelta(days=7), monday, monday-timedelta(days=14)),
        "month": (month_start, first, _previous_month(month_start)),
    }


def _midnight(day: date) -> float:
    # mktime uses the host's historical local offset, including DST changes.
    return time.mktime((day.year, day.month, day.day, 0, 0, 0, 0, 0, -1))


def _utc_stamp(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="microseconds")


def token_label(value: int) -> str:
    number = max(0, int(value))
    chinese = tr("日报") == "日报"
    thresholds = ((100_000_000, "亿"), (10_000, "万")) if chinese else ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for unit, suffix in thresholds:
        if number >= unit:
            return ("%.2f" % (number/unit)).rstrip("0").rstrip(".") + suffix
    return str(number)


def cost_label(value: float | None, complete: bool = True) -> str:
    return ("≥" if not complete else "") + ("$%.2f" % value if value is not None else "—")


def _closed_summary(start: float, end: float) -> dict:
    return dict(start=start, end=end, requests=0, model_calls=0, tokens=0, tokens_complete=True,
                input_tokens=0, cached_tokens=0, cost=0.0, priced=0, cost_complete=True,
                models={}, times=[0, 0, 0, 0], first=None, last=None,
                first_clock=86_400, last_clock=-1)


def _includes(row: dict) -> bool:
    values = row.get("source_ids") or [row.get("source_id")]
    return any(str(value or "") == "local" or str(value or "").startswith("local:") for value in values)


def _add_call(summary: dict, row: dict) -> None:
    tokens = confirmed_tokens(row)
    if tokens is None:
        summary["tokens_complete"] = False
    else:
        summary["tokens"] += tokens
    summary["input_tokens"] += confirmed_count(row.get("input_tokens")) or 0
    summary["cached_tokens"] += confirmed_count(row.get("cached_input_tokens")) or 0
    cost = confirmed_cost(row)
    if cost is None:
        summary["cost_complete"] = False
    else:
        summary["priced"] += 1
        summary["cost"] += cost
    if not confirmed_call(row):
        return
    summary["model_calls"] += 1
    name = str(row.get("model") or tr("未知模型"))
    model = summary["models"].setdefault(name, dict(name=name, calls=0, tokens=0, cost=0.0, priced=0, complete=True))
    model["calls"] += 1
    model["tokens"] += tokens or 0
    if cost is None:
        model["complete"] = False
    else:
        model["cost"] += cost
        model["priced"] += 1


def _add_request(summary: dict, seconds: float) -> None:
    local = time.localtime(seconds)
    clock = local.tm_hour*3600 + local.tm_min*60 + local.tm_sec
    summary["requests"] += 1
    summary["times"][min(3, local.tm_hour // 6)] += 1
    if clock < summary["first_clock"]:
        summary["first_clock"], summary["first"] = clock, seconds
    if clock > summary["last_clock"]:
        summary["last_clock"], summary["last"] = clock, seconds


def _display(period: str, start: date, end: date, current: dict, previous: dict, source_id: str) -> dict:
    models = sorted(current["models"].values(), key=lambda row: (-row["calls"], row["name"]))[:3]
    for model in models:
        model["cost"] = model["cost"] if model.pop("priced") else None
    total = current["cost"] if current["priced"] else None
    names = (tr("凌晨"), tr("上午"), tr("午后"), tr("晚间"))
    peak = max(range(4), key=lambda i: current["times"][i]) if current["requests"] else None
    label = (start.strftime("%Y.%m.%d") + " — " + (end-timedelta(days=1)).strftime("%m.%d")
             if period == "week" else start.strftime("%Y.%m" if period == "month" else "%Y.%m.%d"))
    return dict(period=period, start=start.isoformat(), end=end.isoformat(), date_label=label,
                source_id=source_id, requests=current["requests"], model_calls=current["model_calls"],
                total_tokens=current["tokens"], tokens_complete=current["tokens_complete"],
                input_tokens=current["input_tokens"], cached_tokens=current["cached_tokens"],
                cost=total, cost_complete=current["cost_complete"], models=models,
                time_slices=[dict(name=names[i], requests=value) for i, value in enumerate(current["times"])],
                peak=peak, first=current["first"], last=current["last"],
                previous_tokens=previous["tokens"], comparison_complete=(current["tokens_complete"] and previous["tokens_complete"]))


def collect(query_path: str | Path, now: datetime | None = None) -> dict[str, dict]:
    local = (now or datetime.now().astimezone()).astimezone()
    periods = ranges(local.date())
    bounds = {key: (_midnight(prior), _midnight(start), _midnight(end)) for key, (start, end, prior) in periods.items()}
    summaries = {key: (_closed_summary(current, finish), _closed_summary(prior, current))
                 for key, (prior, current, finish) in bounds.items()}
    floor = min(values[0] for values in bounds.values())
    ceiling = _midnight(local.date())
    path = Path(query_path).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        connection.execute("BEGIN")
        for stamp, payload in connection.execute(
            "SELECT c.timestamp,c.metrics FROM usage_priced_calls c WHERE c.timestamp>=? AND c.timestamp<? "
            "AND EXISTS(SELECT 1 FROM usage_query_sources s WHERE s.record_id=c.id "
            "AND (s.source_id='local' OR s.source_id LIKE 'local:%'))",
            (_utc_stamp(floor), _utc_stamp(ceiling))):
            try:
                seconds = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                row = json.loads(payload)
            except (ValueError, TypeError):
                continue
            for current, previous in summaries.values():
                if current["start"] <= seconds < current["end"]:
                    _add_call(current, row)
                elif previous["start"] <= seconds < previous["end"]:
                    _add_call(previous, row)
        for stamp, payload in connection.execute(
            "SELECT timestamp,data FROM usage_request_groups WHERE timestamp>=? AND timestamp<? "
            "AND record_kind='user_request' AND is_subagent=0",
            (_utc_stamp(floor), _utc_stamp(ceiling))):
            try:
                seconds = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                row = json.loads(payload)
            except (ValueError, TypeError):
                continue
            if not _includes(row):
                continue
            for current, previous in summaries.values():
                if current["start"] <= seconds < current["end"]:
                    _add_request(current, seconds)
                elif previous["start"] <= seconds < previous["end"]:
                    _add_request(previous, seconds)
    finally:
        connection.close()
    source_id = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return {key: _display(key, *periods[key][:2], *summaries[key], source_id) for key in PERIODS}


class ReportStore:
    """One small projection per period per ledger generation and local day."""
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or data_dir() / "Reports")
        self._key = None
        self._documents: dict[str, dict] = {}

    def load(self, query_path: str | Path, generation: int, now: datetime | None = None) -> dict[str, dict]:
        local = (now or datetime.now().astimezone()).astimezone()
        key = (str(Path(query_path).resolve()), generation, local.date(), local.utcoffset(), time.tzname, tr("日报"))
        if key != self._key:
            documents = collect(query_path, local)
            for period, data in documents.items():
                destination = self.directory / data["source_id"] / period / (data["start"] + ".json")
                destination.parent.mkdir(parents=True, exist_ok=True)
                encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
                if not destination.exists() or destination.read_bytes() != encoded:
                    temporary = destination.with_suffix(".tmp")
                    temporary.write_bytes(encoded)
                    temporary.replace(destination)
            self._documents, self._key = documents, key
        return self._documents

    def seen(self, today: date) -> bool:
        try:
            days = json.loads((self.directory / "presentation.json").read_text(encoding="utf-8"))["days"]
            return today.isoformat() in days
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def mark_seen(self, today: date) -> None:
        target = self.directory / "presentation.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            days = set(json.loads(target.read_text(encoding="utf-8"))["days"])
        except (OSError, ValueError, KeyError, TypeError):
            days = set()
        days.add(today.isoformat())
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({"days": sorted(days)[-90:]}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
