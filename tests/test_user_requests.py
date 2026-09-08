from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone

from aiquota.user_requests import aggregate_user_requests, matches_call, turn_key
from aiquota.usage_collector import Collector, scan_directory, _hash, _plain
from aiquota.usage_store import UsageStore

PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"


def record(ident, turn="t1", session=PARENT, cost=1.25, **extra):
    row = dict(id=ident, response_id=ident, turn_id=turn, session_id=session, timestamp="2026-09-08T01:00:00Z",
               model="model-a", service_tier="default", source_id="local", source_name="本机",
               input_tokens=100, cached_input_tokens=60, cache_write_input_tokens=10,
               output_tokens=20, reasoning_output_tokens=5, total_tokens=120,
               cost_usd=cost, pricing_status="priced" if cost is not None else "unpriced")
    row.update(extra)
    return row


def metadata(turn="t1", session=PARENT, status="completed", **extra):
    row = dict(id=turn_key(session, turn), session_id=session, turn_id=turn, verified=True,
               started_at="2026-09-07T23:59:00Z", started_inferred=False,
               ended_at="2026-09-08T01:01:00Z" if status in ("completed", "aborted") else "", status=status,
               prompt_preview="User prompt", output_preview="Final answer" if status == "completed" else "",
               first_turn=True, source_id="local")
    row.update(extra)
    return row


def test_whole_turn_prices_each_call_then_sums_without_double_counting_subsets():
    rows = [record("one"), record("two", model="model-b", service_tier="priority", cost=2.75)]
    groups = aggregate_user_requests(rows, [metadata()])
    assert len(groups) == 1
    group = groups[0]
    assert group["cost_usd"] == 4 and group["call_count"] == 2
    assert group["input_tokens"] == 200 and group["total_tokens"] == 240
    assert group["cached_input_tokens"] == 120 and group["reasoning_output_tokens"] == 10
    assert group["model"] == "多模型（2）" and group["service_tier"] == "mixed"
    assert group["status_label"] == "完成" and group["timestamp"] == "2026-09-07T23:59:00Z"


def test_same_prompt_in_different_turns_is_not_merged_and_unassigned_is_retained():
    rows = [record("a", "t1"), record("b", "t2"), record("c", "")]
    groups = aggregate_user_requests(rows, [metadata("t1"), metadata("t2")])
    assert len(groups) == 3
    assert sum(group["record_kind"] == "unassigned" for group in groups) == 1
    assert math.fsum(group["cost_usd"] for group in groups) == math.fsum(row["cost_usd"] for row in rows)


def test_partial_price_and_no_usage_are_not_reported_as_free_or_complete_amounts():
    groups = aggregate_user_requests([record("a"), record("b", cost=None)], [metadata()])
    assert groups[0]["pricing_status"] == "partial" and groups[0]["cost_usd"] == 1.25
    assert groups[0]["unpriced_calls"] == 1
    empty = aggregate_user_requests([], [metadata(status="running")])[0]
    assert empty["cost_usd"] is None and empty["pricing_status"] == "unmetered"
    assert empty["status_label"] == "回复中"


def test_known_subagent_is_merged_once_and_controls_completion():
    rows = [record("main"), record("child", session=CHILD)]
    child = metadata(session=CHILD, status="running", is_subagent=True, parent_session_id=PARENT,
                     agent_path="/root/helper")
    links = [dict(id="spawn-1", kind="spawn", parent_session_id=PARENT, parent_turn_id="t1", target="/root/helper")]
    group = aggregate_user_requests(rows + [rows[1]], [metadata(), child], links)[0]
    assert group["cost_usd"] == 2.5 and group["call_count"] == 2
    assert group["subagent_count"] == 1 and group["status_label"] == "回复中"
    child.update(status="completed", ended_at="2026-09-08T01:02:00Z")
    assert aggregate_user_requests(rows, [metadata(), child], links)[0]["status_label"] == "完成"


def test_followup_requires_matching_message_identity_and_ambiguous_children_stay_independent():
    child = metadata(session=CHILD, first_turn=False, is_subagent=True, parent_session_id=PARENT,
                     agent_path="/root/helper", input_hashes=["exact-message"])
    rows = [record("a", "t1"), record("b", "t2"), record("child", session=CHILD)]
    turns = [metadata("t1"), metadata("t2"), child]
    links = [dict(id="followup", kind="followup", parent_session_id=PARENT, parent_turn_id="t2",
                  target="/root/helper", message_hash="exact-message")]
    groups = aggregate_user_requests(rows, turns, links)
    assert len(groups) == 2
    assert next(group for group in groups if group["turn_id"] == "t2")["call_count"] == 2
    links.append(dict(links[0], id="ambiguous", parent_turn_id="t1"))
    assert len(aggregate_user_requests(rows, turns, links)) == 3


def test_forks_and_unknown_ancestry_are_not_merged():
    rows = [record("parent"), record("fork", session=CHILD)]
    fork = metadata(session=CHILD, parent_session_id=PARENT, parent_turn_id="t1", is_subagent=False)
    assert len(aggregate_user_requests(rows, [metadata(), fork])) == 2
    orphan = dict(fork, is_subagent=True, parent_turn_id="missing")
    assert len(aggregate_user_requests(rows, [metadata(), orphan])) == 2


def test_explicit_spawn_child_id_is_sufficient_without_agent_path_metadata():
    rows = [record("a"), record("b", session=CHILD)]
    links = [dict(id="spawn", kind="spawn", parent_session_id=PARENT, parent_turn_id="t1", child_session_id=CHILD)]
    groups = aggregate_user_requests(rows, [metadata(), metadata(session=CHILD)], links)
    assert len(groups) == 1 and groups[0]["call_count"] == 2


def test_parent_can_contain_child_cost_before_its_own_meter_arrives():
    parent = metadata(status="running", synthetic=True)
    child = metadata(session=CHILD, is_subagent=True, parent_session_id=PARENT, parent_turn_id="t1")
    links = [dict(id="spawn", kind="spawn", parent_session_id=PARENT, parent_turn_id="t1", child_session_id=CHILD)]
    groups = aggregate_user_requests([record("child", session=CHILD, source_id="ssh:server")], [parent, child], links)
    assert len(groups) == 1 and groups[0]["session_id"] == PARENT
    assert groups[0]["status_label"] == "回复中" and groups[0]["cost_usd"] == 1.25


def test_missing_child_logs_do_not_claim_the_whole_request_is_complete():
    link = dict(id="spawn", kind="spawn", parent_session_id=PARENT, parent_turn_id="t1", child_session_id=CHILD)
    group = aggregate_user_requests([record("parent")], [metadata()], [link])[0]
    assert group["status_label"] == "未知"
    assert "尚未采集" in group["association_note"]


def test_agent_cycle_does_not_lose_records_or_recurse_forever():
    first = metadata(is_subagent=True, parent_session_id=CHILD, parent_turn_id="t1")
    second = metadata(session=CHILD, is_subagent=True, parent_session_id=PARENT, parent_turn_id="t1")
    groups = aggregate_user_requests([record("a"), record("b", session=CHILD)], [first, second])
    assert len(groups) == 2 and sum(group["call_count"] for group in groups) == 2


def test_filter_conditions_must_match_the_same_call_and_do_not_reprice_a_group():
    a = record("a", model="model-a", source_id="local")
    b = record("b", model="model-b", source_id="ssh:server", source_name="服务器", service_tier="priority")
    group = aggregate_user_requests([a, b], [metadata()])[0]
    assert not any(matches_call(row, source="ssh:server", model="model-a") for row in (a, b))
    assert any(matches_call(row, source="ssh:server", model="model-b", tier="priority") for row in (a, b))
    assert group["cost_usd"] == a["cost_usd"] + b["cost_usd"]


def event(kind, payload, second=0):
    stamp = datetime(2026, 9, 8, tzinfo=timezone.utc) + timedelta(seconds=second)
    return dict(type=kind, timestamp=stamp.isoformat(), payload=payload)


def meter(response="r1", second=2, turn="t1", session=PARENT):
    return event("token_usage_record", dict(thread_id=session, turn_id=turn, response_id=response,
        usage=dict(input_tokens=100, cached_input_tokens=50, output_tokens=10, total_tokens=110)), second)


def write_file(root, entries, session=PARENT):
    folder = root / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("rollout-" + session + ".jsonl")
    path.write_text("".join(json.dumps(item) + "\n" for item in entries), encoding="utf-8")
    return path


def test_lifecycle_and_late_final_answer_update_without_a_new_meter(tmp_path):
    root = tmp_path / "codex"
    entries = [event("session_meta", dict(id=PARENT, model_provider="openai")),
               event("event_msg", dict(type="task_started", turn_id="t1")),
               event("turn_context", dict(turn_id="t1", model="model-a")),
               event("event_msg", dict(type="user_message", message="real user input"), 1), meter()]
    path = write_file(root, entries)
    store = UsageStore(tmp_path / "usage.sqlite")
    collector = Collector(store)
    collector.scan(root)
    assert store.turns()[0]["status"] == "running"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event("event_msg", dict(type="task_complete", turn_id="t1", last_agent_message="final response"), 4)) + "\n")
    status = collector.scan(root)
    assert status["changed"] > 0 and len(store.records()) == 1
    turn = store.turns()[0]
    assert turn["status"] == "completed" and turn["output_preview"] == "final response"
    assert turn["prompt_preview"] == "real user input"
    assert collector.scan(root)["bytes_read"] == 0


def test_remote_and_local_metadata_match_and_rewrites_reconcile(tmp_path):
    root = tmp_path / "codex"
    entries = [event("session_meta", dict(id=PARENT, model_provider="openai")),
               event("event_msg", dict(type="task_started", turn_id="t1")), meter(),
               event("event_msg", dict(type="turn_aborted", turn_id="t1"), 3)]
    path = write_file(root, entries)
    local, remote = UsageStore(tmp_path / "local.sqlite"), UsageStore(tmp_path / "remote.sqlite")
    Collector(local).scan(root)
    frames = scan_directory(root)
    remote.import_frames(frames, "local", "cursor:test")
    assert local.records() == remote.records()
    assert [(row["id"], row["status"]) for row in local.turns()] == [(row["id"], row["status"]) for row in remote.turns()]
    assert remote.import_frames(frames, "local", "cursor:test") == 0
    path.write_text(json.dumps(entries[0]) + "\n", encoding="utf-8")
    Collector(local).scan(root)
    assert local.records() == [] and local.turns() == []


def test_collects_only_agent_routing_identities_and_links_actual_child(tmp_path):
    root = tmp_path / "codex"
    message = "Do a bounded task"
    parent = [event("session_meta", dict(id=PARENT, model_provider="openai")),
              event("event_msg", dict(type="task_started", turn_id="t1")), meter("parent", 1),
              event("response_item", dict(type="function_call", name="spawn_agent", call_id="call-1",
                    arguments=json.dumps(dict(task_name="helper", message=message))), 2),
              event("response_item", dict(type="function_call_output", call_id="call-1", output=json.dumps(dict(task_name="/root/helper"))), 3)]
    write_file(root, parent)
    child = [event("session_meta", dict(id=CHILD, model_provider="openai", subagent_history_start_ordinal=0,
             source=dict(subagent=dict(thread_spawn=dict(parent_thread_id=PARENT, agent_path="/root/helper"))))),
             event("event_msg", dict(type="task_started", turn_id="c1"), 4),
             event("event_msg", dict(type="user_message", message=message), 5), meter("child", 6, "c1", CHILD)]
    write_file(root, child, CHILD)
    store = UsageStore(tmp_path / "usage.sqlite")
    Collector(store).scan(root)
    assert len(store.agent_links()) == 1
    link = store.agent_links()[0]
    assert link["target"] == "/root/helper" and "message" not in link
    rows = [dict(row, cost_usd=1) for row in store.records()]
    groups = aggregate_user_requests(rows, store.turns(), store.agent_links())
    assert len(groups) == 1 and groups[0]["cost_usd"] == 2


def test_legacy_user_boundaries_are_stable_and_repeated_prompts_stay_separate(tmp_path):
    root = tmp_path / "codex"
    entries = [event("session_meta", dict(id=PARENT, model_provider="openai")),
               event("event_msg", dict(type="user_message", message="continue"), 1), meter("one", 2, ""),
               event("event_msg", dict(type="task_complete", last_agent_message="done"), 3),
               event("event_msg", dict(type="user_message", message="continue"), 4), meter("two", 5, "")]
    write_file(root, entries)
    store = UsageStore(tmp_path / "usage.sqlite")
    Collector(store).scan(root)
    turns = {row["request_turn_id"] for row in store.records()}
    assert len(turns) == 2 and all(turn.startswith("legacy-user:") for turn in turns)
    assert all(not row["turn_id"] for row in store.records())
    assert len(aggregate_user_requests(store.records(), store.turns())) == 2


def test_late_old_meter_does_not_steal_the_next_user_prompt(tmp_path):
    root = tmp_path / "codex"
    entries = [event("session_meta", dict(id=PARENT, model_provider="openai")),
               event("event_msg", dict(type="task_started", turn_id="t1")),
               event("event_msg", dict(type="user_message", message="old prompt"), 1),
               event("event_msg", dict(type="task_complete", turn_id="t1", last_agent_message="old answer"), 2),
               event("event_msg", dict(type="user_message", message="new prompt"), 3),
               event("response_item", dict(type="message", role="assistant", phase="final_answer",
                    content=[dict(type="output_text", text="late old answer")]), 3),
               meter("old-late", 4, "t1"),
               event("event_msg", dict(type="task_started", turn_id="t2"), 5), meter("new", 6, "t2")]
    write_file(root, entries)
    store = UsageStore(tmp_path / "usage.sqlite")
    Collector(store).scan(root)
    groups = {row["turn_id"]: row for row in aggregate_user_requests(store.records(), store.turns())}
    assert set(groups) == {"t1", "t2"}
    assert groups["t1"]["prompt_preview"] == "old prompt" and groups["t1"]["status_label"] == "完成"
    assert groups["t1"]["output_preview"] == "late old answer"
    assert groups["t2"]["prompt_preview"] == "new prompt" and groups["t2"]["call_count"] == 1


def test_duplicate_user_item_lifecycle_is_one_legacy_request(tmp_path):
    root = tmp_path / "codex"
    item = dict(type="user_message", id="message-1", content="same user item")
    entries = [event("session_meta", dict(id=PARENT, model_provider="openai")),
               event("event_msg", dict(type="item_started", item=item), 1), meter("one", 2, ""),
               event("event_msg", dict(type="item_completed", item=item), 3), meter("two", 4, "")]
    write_file(root, entries)
    store = UsageStore(tmp_path / "usage.sqlite")
    Collector(store).scan(root)
    groups = aggregate_user_requests(store.records(), store.turns())
    assert len(groups) == 1 and groups[0]["call_count"] == 2


def test_version_three_index_backfills_metadata_without_changing_accounting(tmp_path):
    root = tmp_path / "codex"
    write_file(root, [event("session_meta", dict(id=PARENT, model_provider="openai")),
                     event("event_msg", dict(type="task_started", turn_id="t1")), meter(),
                     event("event_msg", dict(type="task_complete", turn_id="t1"), 3)])
    path = tmp_path / "usage.sqlite"
    store = UsageStore(path)
    Collector(store).scan(root)
    with sqlite3.connect(path) as db:
        original = json.loads(db.execute("SELECT data FROM usage_records").fetchone()[0])
        original.pop("request_turn_id", None)
        db.execute("UPDATE usage_records SET data=?", (json.dumps(original),))
        key, raw = db.execute("SELECT key,data FROM usage_cursors").fetchone()
        cursor = json.loads(raw)
        cursor["version"] = 3
        cursor["state"] = {k: v for k, v in cursor["state"].items() if not k.startswith("request_")}
        db.execute("UPDATE usage_cursors SET data=? WHERE key=?", (json.dumps(cursor), key))
        db.execute("DROP TABLE usage_turns")
        db.execute("DROP TABLE usage_agent_links")
        db.execute("DELETE FROM usage_origins WHERE kind IN ('turn','agent_link')")
    upgraded = UsageStore(path)
    assert Collector(upgraded).scan(root)["bytes_read"] > 0
    assert len(upgraded.records()) == 1 and upgraded.turns()[0]["status"] == "completed"
    new = dict(upgraded.records()[0])
    new.pop("request_turn_id", None)
    assert new == original
    assert Collector(upgraded).scan(root)["bytes_read"] == 0
