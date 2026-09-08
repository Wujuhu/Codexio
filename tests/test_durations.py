from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from aiquota.durations import duration_text, elapsed_milliseconds
from aiquota.user_requests import aggregate_user_requests, turn_key
from aiquota.usage_collector import Collector, PARSER_VERSION, cursor_key, scan_directory
from aiquota.usage_store import UsageStore
from aiquota.usage_queries import UsageQueries
from aiquota.pricing import PricingCatalog
from aiquota.dashboard import Dashboard, CALL_HEADERS, USER_REQUEST_HEADERS, DURATION_COLUMN, SESSION_COLUMN
from aiquota.settings import AppSettings

BASE = datetime(2026, 9, 8, tzinfo=timezone.utc)


def stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat()


def call(ident="one", session="parent", turn="p", second=9, **extra):
    return dict(id=ident, session_id=session, turn_id=turn, timestamp=stamp(second), model="gpt-6-astra",
                service_tier="default", source_id="local", provider="openai", limit_id="codex", input_tokens=100,
                cached_input_tokens=0, cache_write_input_tokens=0, output_tokens=10, reasoning_output_tokens=0,
                total_tokens=110, cost_usd=1.0, pricing_status="priced", quality="response", **extra)


def turn(session="parent", ident="p", start=0, end=10, status="completed", **extra):
    return dict(id=turn_key(session, ident), session_id=session, turn_id=ident, verified=True, has_usage=True,
                started_at=stamp(start), started_inferred=False, ended_at=stamp(end) if end is not None else "",
                status=status, **extra)


@pytest.mark.parametrize("milliseconds,text", [(None,"—"),(True,"—"),(-1,"—"),(float("nan"),"—"),
    (0,"0.00 秒"),(420,"0.42 秒"),(1250,"1.2 秒"),(90400,"1分30秒"),
    (3723000,"1时02分03秒"),(93780000,"1天02时03分")])
def test_duration_format_and_invalid_values(milliseconds, text):
    assert duration_text({"duration_ms": milliseconds}) == text


def test_reported_turn_duration_keeps_subsecond_precision():
    row = aggregate_user_requests([call()], [turn(duration_ms=10543)])[0]
    assert row["duration_ms"] == 10543
    assert not row["duration_running"]


def test_parallel_child_uses_end_to_end_span_not_sum():
    rows = [call(), call("two", "child", "c", 29)]
    metadata = [turn(duration_ms=10000), turn("child","c",2,30,is_subagent=True,
                    parent_session_id="parent",parent_turn_id="p",duration_ms=28000)]
    group = aggregate_user_requests(rows, metadata)[0]
    assert group["call_count"] == 2
    assert group["duration_ms"] == 30000
    assert group["cost_usd"] == 2


def test_running_child_keeps_whole_request_clock_running():
    rows = [call(), call("two", "child", "c", 15)]
    metadata = [turn(),turn("child","c",2,None,"running",is_subagent=True,parent_session_id="parent",parent_turn_id="p")]
    group = aggregate_user_requests(rows, metadata)[0]
    assert group["request_status"] == "running"
    assert elapsed_milliseconds(group, BASE+timedelta(seconds=25)) == 25000


def test_unknown_end_or_inferred_start_does_not_fabricate_total():
    metadata = turn(status="unknown",end=None)
    assert aggregate_user_requests([call()], [metadata])[0]["duration_ms"] is None
    metadata = turn()
    metadata["started_inferred"] = True
    assert duration_text(aggregate_user_requests([call()], [metadata])[0]) == "—"
    metadata = turn(start=20,end=10)
    assert duration_text(aggregate_user_requests([call()], [metadata])[0]) == "—"


def test_aborted_request_stops_at_recorded_interruption():
    group = aggregate_user_requests([call()], [turn(end=15,status="aborted")])[0]
    assert group["duration_ms"] == 15000 and not group["duration_running"]


def fixture_log(root):
    from test_usage_collector import event, meta, turn as context, modern, write
    usage = modern(second=6)
    usage["payload"]["duration_ms"] = 1250
    entries = [meta(), event("event_msg",dict(type="task_started",turn_id="turn-1"),1), context(), usage,
               event("event_msg",dict(type="item_completed",started_at_ms=1,completed_at_ms=99999,
                     item=dict(type="CommandExecution",duration=dict(secs=99,nanos=0))),7),
               event("event_msg",dict(type="task_complete",turn_id="turn-1",duration_ms=20543),21)]
    return write(root, entries)


def test_local_and_ssh_timing_and_v4_backfill_preserve_counters(tmp_path):
    root=tmp_path/"codex"
    path=fixture_log(root)
    store=UsageStore(tmp_path/"local.sqlite")
    collector=Collector(store)
    collector.scan(root)
    before=store.records()[0]
    assert before["duration_ms"] == 1250
    assert store.turns()[0]["duration_ms"] == 20543
    frames=scan_directory(root,{})
    remote=UsageStore(tmp_path/"remote.sqlite")
    remote.import_frames(frames,"local")
    assert remote.records()[0]["duration_ms"] == 1250
    assert remote.turns()[0]["duration_ms"] == 20543
    with store._connect() as db:
        for table in ("usage_records","usage_turns"):
            for ident, raw in db.execute("SELECT id,data FROM "+table).fetchall():
                row=json.loads(raw);row.pop("duration_ms",None)
                db.execute("UPDATE "+table+" SET data=? WHERE id=?",(json.dumps(row),ident))
    key=cursor_key(root,path,"local")
    cursor=store.get_cursor(key);cursor["version"]=PARSER_VERSION-1
    store.set_cursor(key,cursor)
    collector.scan(root)
    after=store.records()[0]
    assert after == before
    assert store.turns()[0]["duration_ms"] == 20543
    assert store.count_records() == 1
    collector.scan(root)
    assert store.records()[0] == before


def test_raw_call_never_uses_turn_or_tool_duration(tmp_path):
    from test_usage_collector import event,meta,turn as context,modern,write
    root=tmp_path/"codex"
    write(root,[meta(),context(),modern(),event("event_msg",dict(type="task_complete",turn_id="turn-1",duration_ms=99999),9)])
    store=UsageStore(tmp_path/"usage.sqlite");Collector(store).scan(root)
    assert store.records()[0].get("duration_ms") is None
    assert store.turns()[0]["duration_ms"] == 99999


def test_copied_call_cannot_overwrite_original_timing(tmp_path):
    store=UsageStore(tmp_path/"usage.sqlite")
    row=call(response_id="response",duration_ms=1234,context_owner_verified=True)
    store.upsert_records([row],"local")
    copy=dict(row,duration_ms=9999,context_owner_verified=False)
    store.upsert_records([copy],"ssh:copy")
    assert store.records()[0]["duration_ms"] == 1234


def test_database_pages_and_details_keep_timing(tmp_path):
    store=UsageStore(tmp_path/"usage.sqlite")
    store.upsert_records([call(duration_ms=1250)],"local")
    store.upsert_turns([turn(duration_ms=10543)],"local")
    queries=UsageQueries(store.path)
    queries.rebuild(PricingCatalog(tmp_path/"prices"))
    group=queries.page()["rows"][0]
    assert group["duration_ms"] == 10543
    assert queries.page("model_call")["rows"][0]["duration_ms"] == 1250
    assert queries.request_members(group["id"])["rows"][0]["duration_ms"] == 1250


def test_duration_column_and_visible_only_running_clock(monkeypatch):
    from aiquota import dashboard as module
    app=QApplication.instance() or QApplication([])
    now=datetime.now(timezone.utc)
    start=now-timedelta(seconds=90)
    record=call(second=1)
    record["timestamp"]=now.isoformat()
    metadata=turn(end=None,status="running")
    metadata["started_at"]=start.isoformat()
    window=Dashboard(AppSettings(),{}, {})
    window.apply_data({"records":[record],"turns":[metadata]})
    window.open_page("logs","all")
    assert CALL_HEADERS[5:8] == ["费用","耗时","Session ID"]
    assert len(CALL_HEADERS)==9 and len(USER_REQUEST_HEADERS)==10
    assert window._log_table.item(0,DURATION_COLUMN).text() != "—"
    assert window._log_table.cellWidget(0,SESSION_COLUMN) is not None
    assert window._duration_timer.isActive()
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return start+timedelta(seconds=123)
    monkeypatch.setattr(module,"datetime",Clock)
    window._refresh_duration_cells()
    assert window._log_table.item(0,DURATION_COLUMN).text() == "2分03秒"
    window.open_page("settings")
    assert not window._duration_timer.isActive()
    window.open_page("logs")
    assert window._duration_timer.isActive()
    window.hide()
    assert not window._duration_timer.isActive()
    window.show()
    assert window._duration_timer.isActive()
    window._log_mode.setCurrentIndex(window._log_mode.findData("model_call"))
    assert not window._duration_timer.isActive()
    assert window._log_table.item(0,DURATION_COLUMN).text() == "—"
    window.close()
    app.processEvents()
