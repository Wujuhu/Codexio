from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from codexio.usage_collector import Collector, cursor_key, read_session_titles, scan_directory
from codexio.usage_store import UsageStore

PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"


def usage(i=100, r=40, o=10, w=0, total=None):
    return dict(input_tokens=i, cached_input_tokens=r, cache_write_input_tokens=w,
                output_tokens=o, reasoning_output_tokens=2 if o >= 2 else 0, total_tokens=i + o if total is None else total)


def event(kind, payload, second=1, **extra):
    return dict(type=kind, timestamp="2026-09-07T00:00:{:02d}Z".format(second), payload=payload, **extra)


def meta(ident=PARENT, **fields):
    return event("session_meta", dict(id=ident, model_provider="openai", **fields), 0)


def turn(ident="turn-1", model="gpt-test"):
    return event("turn_context", dict(turn_id=ident, model=model), 1)


def modern(response="resp-1", u=None, second=2, ident=PARENT, turn_id="turn-1"):
    value = u or usage()
    return event("token_usage_record", dict(thread_id=ident, turn_id=turn_id, response_id=response,
                                           usage=value, turn_token_usage=value, thread_token_usage=value), second)


def legacy(total=None, last=None, second=3, lane="codex", **extra):
    return event("event_msg", dict(type="token_count", info=dict(total_token_usage=total or usage(), last_token_usage=last),
                                  rate_limits=dict(limit_id=lane, **extra)), second)


def write(root, entries, ident=PARENT, archived=False):
    directory = root / ("archived_sessions" if archived else "sessions/2026/09/07")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("rollout-2026-09-07T00-00-00-" + ident + ".jsonl")
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for entry in entries:
            stream.write(json.dumps(entry) + "\n")
    return path


def append(path, entries):
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        for entry in entries:
            stream.write(json.dumps(entry) + "\n")


def setup(tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    store = UsageStore(tmp_path / "widget.sqlite")
    return root, store, Collector(store)


def test_modern_legacy_pair_ignores_different_cumulative_and_has_write(tmp_path):
    root, store, collector = setup(tmp_path)
    u = usage(w=60)
    write(root, [meta(), turn(), modern(u=u), legacy(usage(9999, 9000, 900), u)])
    collector.scan(root)
    rows = store.records()
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 110
    assert rows[0]["cache_write_input_tokens"] == 60
    assert rows[0]["quality"] == "response"


def test_delayed_pair_survives_incremental_scan_and_tools(tmp_path):
    root, store, collector = setup(tmp_path)
    path = write(root, [meta(), turn(), modern()])
    collector.scan(root)
    delayed = legacy(usage(10000, 5000, 200), usage())
    delayed["timestamp"] = "2026-09-07T06:00:00Z"
    append(path, [event("response_item", {"role": "tool", "content": "never index this"}), delayed])
    result = collector.scan(root)
    assert store.count_records() == 1
    assert result["bytes_read"] < path.stat().st_size
    assert collector.scan(root)["bytes_read"] == 0


def test_two_modern_responses_equal_usage_are_distinct_and_cross_source_dedup(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern(), legacy(usage(), usage()), modern("resp-2", second=4), legacy(usage(200, 80, 20), usage(), second=5)])
    collector.scan(root)
    assert store.count_records() == 2
    collector.scan(root, source_id="ssh:server", source_name="Server")
    assert store.count_records() == 2
    assert store.count_records(source_id="ssh:server") == 2
    assert {r["response_id"] for r in store.records()} == {"resp-1", "resp-2"}


def test_missing_modern_next_response_still_imports_legacy(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern(), legacy(usage(), usage()),
                 event("event_msg", {"type": "raw_response_completed", "response_id": "other"}, 4),
                 legacy(usage(200, 80, 20), usage(), second=5)])
    collector.scan(root)
    assert store.count_records() == 2


def test_later_legacy_without_response_marker_never_overwrites_modern(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern(), legacy(usage(), usage()),
                 legacy(usage(200, 80, 20), usage(), second=5)])
    collector.scan(root)
    assert store.count_records() == 2
    assert sorted(r["quality"] for r in store.records()) == ["legacy_last", "response"]


def test_synthetic_context_total_is_never_consumption(tmp_path):
    root, store, collector = setup(tmp_path)
    synthetic = usage(0, 0, 0, total=258400)
    write(root, [meta(), turn(), legacy(usage(), usage()), legacy(synthetic, synthetic, second=4),
                 legacy(usage(50, 0, 5), usage(50, 0, 5), second=5)])
    collector.scan(root)
    assert [r["total_tokens"] for r in store.records()] == [55, 110]


def test_multilane_duplicates_and_real_counter_reset(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), legacy(usage(1000, 400, 100), usage()),
                 legacy(usage(2000, 900, 90), usage(200, 100, 20), second=4, lane="other"),
                 legacy(usage(1000, 400, 100), usage(), second=5),
                 legacy(usage(1100, 440, 110), usage(), second=6),
                 legacy(usage(50, 10, 5), usage(50, 10, 5), second=7)])
    collector.scan(root)
    assert sorted(r["input_tokens"] for r in store.records()) == [50, 100, 100, 200]


def test_cumulative_fallback_keeps_baseline_across_model_switch(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(model="a"), legacy(usage(), {}), turn(model="b"), legacy(usage(150, 50, 15), None, second=5)])
    collector.scan(root)
    assert [(r["model"], r["input_tokens"]) for r in store.records()] == [("b", 50), ("a", 100)]


def test_partial_tail_is_only_committed_when_newline_arrives(tmp_path):
    root, store, collector = setup(tmp_path)
    path = write(root, [meta(), turn()])
    with path.open("ab") as stream:
        stream.write(json.dumps(modern()).encode("utf-8"))
    collector.scan(root)
    assert store.count_records() == 0
    key = cursor_key(root, path, "local")
    assert store.get_cursor(key)["offset"] < path.stat().st_size
    with path.open("ab") as stream:
        stream.write(b"\n")
    collector.scan(root)
    assert store.count_records() == 1


def test_archived_move_does_not_recount_and_append_is_read(tmp_path):
    root, store, collector = setup(tmp_path)
    path = write(root, [meta(), turn(), modern()])
    collector.scan(root)
    target = root / "archived_sessions" / path.name
    target.parent.mkdir()
    path.rename(target)
    append(target, [modern("resp-2", second=4)])
    collector.scan(root)
    assert store.count_records() == 2


def test_paginated_fork_does_not_require_parent_and_boundary_skips_prefix(tmp_path):
    root, store, collector = setup(tmp_path)
    inherited = modern("old", ident=PARENT)
    inherited["ordinal"] = 1
    own = modern("new", ident=CHILD, second=4)
    own["ordinal"] = 11
    write(root, [meta(CHILD, history_mode="paginated", forked_from_id=PARENT,
                      history_base={"thread_id": PARENT, "end_ordinal_exclusive": 30, "end_byte_offset": 400},
                      subagent_history_start_ordinal=10), turn(), inherited, own], ident=CHILD)
    collector.scan(root)
    assert [r["response_id"] for r in store.records()] == ["new"]


def test_legacy_fork_replay_and_missing_parent_recovery(tmp_path):
    root, store, collector = setup(tmp_path)
    child_meta = meta(CHILD, forked_from_id=PARENT)
    child_meta["timestamp"] = "2026-09-07T00:00:10Z"
    write(root, [child_meta, turn(), legacy(usage(), usage(), second=11),
                 legacy(usage(200, 80, 20), usage(), second=12)], ident=CHILD)
    collector.scan(root)
    assert store.count_records() == 0
    write(root, [meta(), turn(), legacy(usage(), usage())])
    collector.scan(root)
    assert store.count_records() == 2


def test_titles_read_only_and_preview_is_user_message_not_tools(tmp_path):
    root, store, collector = setup(tmp_path)
    db = sqlite3.connect(str(root / "state_5.sqlite"))
    db.execute("CREATE TABLE threads (id TEXT,title TEXT,secret TEXT)")
    db.execute("INSERT INTO threads VALUES(?,?,?)", (PARENT, "Task title", "not to be read"))
    db.commit()
    db.close()
    write(root, [meta(), turn(), event("event_msg", {"type": "user_message", "message": "hello " * 100}),
                 event("response_item", {"role": "system", "content": "SYSTEM_SECRET"}),
                 event("event_msg", {"type": "exec_command_end", "aggregated_output": "TOOL_SECRET"}), modern()])
    collector.scan(root)
    row = store.records()[0]
    assert row["session_title"] == "Task title"
    assert len(row["prompt_preview"]) <= 600 and row["prompt_preview"].startswith("hello")
    assert "SECRET" not in json.dumps(row)


def test_session_index_display_title_takes_priority_over_old_database_title(tmp_path):
    index = [dict(id=PARENT, thread_name="Old display name"),
             dict(id=PARENT, thread_name="Current display name"),
             dict(id=CHILD, thread_name="  ")]
    (tmp_path / "session_index.jsonl").write_text(
        "\n".join(json.dumps(row) for row in index), encoding="utf-8")
    with sqlite3.connect(str(tmp_path / "state_5.sqlite")) as db:
        db.execute("CREATE TABLE threads (id TEXT, title TEXT)")
        db.executemany("INSERT INTO threads VALUES (?, ?)", [
            (PARENT, "# Files mentioned by the user: attachment wrapper"),
            (CHILD, "Database fallback"),
        ])
    assert read_session_titles(tmp_path) == {PARENT: "Current display name", CHILD: "Database fallback"}


def test_only_known_injected_title_prefixes_are_filtered(tmp_path):
    index = [dict(id="files", thread_name="# Files mentioned by the user: file.png"),
             dict(id="agents", thread_name="# AGENTS.md instructions <INSTRUCTIONS>"),
             dict(id="heading", thread_name="# My actual project heading"),
             dict(id="mention", thread_name="Discuss # AGENTS.md instructions behavior"),
             dict(id="fallback", thread_name="# AGENTS.md instructions", title="Valid index fallback")]
    (tmp_path / "session_index.jsonl").write_text(
        "\n".join(json.dumps(row) for row in index), encoding="utf-8")
    with sqlite3.connect(str(tmp_path / "state_5.sqlite")) as db:
        db.execute("CREATE TABLE threads (id TEXT, title TEXT)")
        db.executemany("INSERT INTO threads VALUES (?, ?)", [
            ("files", "# Files mentioned by the user: still generated"),
            ("agents", "# AGENTS.md instructions generated"),
            ("only-db", "# My database title"),
        ])
    assert read_session_titles(tmp_path) == {
        "heading": "# My actual project heading",
        "mention": "Discuss # AGENTS.md instructions behavior",
        "fallback": "Valid index fallback",
        "only-db": "# My database title",
    }


def test_invalid_later_index_entry_does_not_replace_valid_display_title(tmp_path):
    index = [dict(id=PARENT, thread_name="Renamed task"),
             dict(id=PARENT, thread_name="# Files mentioned by the user: later injection")]
    (tmp_path / "session_index.jsonl").write_text(
        "\n".join(json.dumps(row) for row in index), encoding="utf-8")
    assert read_session_titles(tmp_path) == {PARENT: "Renamed task"}


def test_observations_and_unknown_historical_account(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), legacy(usage(), usage(), plan_type="pro", secondary={"used_percent": 12, "window_minutes": 10080, "resets_at": 1790000000})])
    collector.scan(root, account_since="2026-09-07T00:00:02Z")
    assert store.observations()[0]["account_key"] == "current"
    assert store.observations()[0]["window_minutes"] == 10080


def test_scan_directory_matches_local_and_large_integer_does_not_wrap(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern(u=usage(5_000_000_000, 4_000_000_000, 500))])
    frames = scan_directory(root)
    collector.scan(root)
    assert frames["records"][0]["input_tokens"] == store.records()[0]["input_tokens"] == 5_000_000_000
    assert not scan_directory(root, frames["cursors"])["records"]


def test_store_filters_threads_and_pages(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern()])
    collector.scan(root)
    base = store.records()[0]
    def put(index):
        row = dict(base, id=str(index), response_id="response-{}".format(index))
        return store.upsert_records([row], "local" if index % 2 else "ssh:test")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(put, range(10)))
    assert store.count_records() == 11
    assert store.count_records(start="2026-09-07", end="2026-09-08", model="gpt-test") == 11
    assert store.count_records(end="2026-09-07") == 0
    assert len(store.records(limit=3, offset=2)) == 3
    assert len(store.records(source_id="local", limit=2)) == 2
    assert store.records(limit=2, offset=2) != store.records(limit=2)
    store.set_meta("ssh-cursors", {"offset": 10})
    store.clear_index()
    assert store.count_records() == 0 and store.get_meta("ssh-cursors") is None


def test_rewrite_keeps_stable_response_ids_and_indexes_new_content(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern()])
    collector.scan(root)
    write(root, [meta(), turn(), modern(), modern("resp-2", second=5)])
    collector.scan(root)
    assert store.count_records() == 2


def test_old_user_role_fallback_skips_generated_wrappers(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), event("response_item", {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "# AGENTS.md instructions <INSTRUCTIONS>hidden wrapper</INSTRUCTIONS>"}]}),
        event("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Please fix the parser"}]}), modern()])
    status = collector.scan(root)
    assert store.records()[0]["prompt_preview"] == "Please fix the parser"
    assert store.records()[0]["service_tier"] is None
    assert status["status"] == "ok" and status["name"] == "本机"


def test_user_preview_filters_context_blocks_before_joining_and_limiting():
    from codexio.usage_collector import _user_preview
    plugin_context = "<recommended_plugins>" + "generated catalog " * 1600 + "</recommended_plugins>"
    blocks = [{"type": "input_text", "text": plugin_context},
              {"type": "input_text", "text": "# AGENTS.md instructions <INSTRUCTIONS>rules</INSTRUCTIONS>"},
              {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]
    assert _user_preview(blocks) == ""
    assert _user_preview(blocks + [{"type": "text", "text": "Review my permutation code."}]) == "Review my permutation code."
    assert _user_preview(plugin_context + "\nReal question") == "Real question"
    assert _user_preview("<recommended_plugins>truncated catalog") == ""
    assert _user_preview("Explain the <recommended_plugins> tag") == "Explain the <recommended_plugins> tag"
    assert _user_preview(plugin_context + "\n## My request:\nActual question") == "Actual question"


def test_parser_upgrade_replaces_wrapper_title_without_changing_usage_or_cost(tmp_path, monkeypatch):
    from codexio import usage_collector as module
    from codexio import usage_queries as query_module
    from codexio.usage_queries import UsageQueries
    from codexio.pricing import PricingCatalog
    root, store, collector = setup(tmp_path)
    question = "Review my Collections.swap permutation implementation."
    content = [{"type": "input_text", "text": "<recommended_plugins>Catalog</recommended_plugins>"},
               {"type": "input_text", "text": "# AGENTS.md instructions <INSTRUCTIONS>Rules</INSTRUCTIONS>"}]
    entries = [meta(), event("event_msg", dict(type="task_started", turn_id="turn-1")),
               event("response_item", dict(type="message", role="user", content=content), 2),
               event("turn_context", dict(turn_id="turn-1", model="gpt-6-astra"), 3),
               event("response_item", dict(type="message", role="user", content=[dict(type="input_text", text=question)]), 4),
               event("event_msg", dict(type="item_completed", item=dict(type="UserMessage", id="user-item",
                     content=[dict(type="text", text=question)])), 5),
               modern(second=6), legacy(last=usage(), second=7),
               event("event_msg", dict(type="task_complete", turn_id="turn-1"), 8)]
    write(root, entries)
    queries, prices = UsageQueries(store.path), PricingCatalog(tmp_path / "prices")
    def previous_preview(value):
        text = module._plain(value, 12000)
        return "" if text.startswith(("# AGENTS.md instructions", "<environment_context>")) else text[:600]
    with monkeypatch.context() as old:
        old.setattr(module, "PARSER_VERSION", 5)
        old.setattr(module, "_user_preview", previous_preview)
        old.setattr(query_module, "_user_preview", previous_preview)
        collector.scan(root)
        queries.rebuild(prices)
        before = queries.page()["rows"][0]
        before_records = store.records()
        assert before["prompt_preview"].startswith("<recommended_plugins>")
        assert before_records[0]["prompt_preview"] == question
    assert collector.scan(root)["bytes_read"] > 0
    queries.rebuild(prices)
    after = queries.page()["rows"][0]
    assert after["prompt_preview"] == question
    assert store.records() == before_records
    assert after["call_count"] == before["call_count"] == 1
    assert after["total_tokens"] == before["total_tokens"] == 110
    assert after["cost_usd"] == before["cost_usd"] and after["cost_usd"] > 0
    assert Collector(store).scan(root)["bytes_read"] == 0


def test_preview_upgrade_simplifies_real_skill_and_image_parts_without_recount(tmp_path, monkeypatch):
    from codexio import usage_collector as module
    from codexio.pricing import PricingCatalog
    from codexio.usage_queries import UsageQueries
    root, store, collector = setup(tmp_path)
    question = "[$impeccable](C:/skills/impeccable/SKILL.md) 调整图片里的布局。"
    response_parts = [dict(type="input_text", text=question),
                      dict(type="input_text", text='<image name=[Image #1] path="C:/temp/a.png">'),
                      dict(type="input_image", image_url="data:image/png;base64,unused"),
                      dict(type="input_text", text="</image>")]
    write(root, [meta(), event("event_msg", dict(type="task_started", turn_id="turn-1")), turn(),
                 event("response_item", dict(type="message", role="user", content=response_parts), 2),
                 event("event_msg", dict(type="item_completed", item=dict(type="UserMessage", id="user-message",
                       content=[dict(type="text", text=question), dict(type="local_image", path="C:/temp/a.png")])), 3),
                 modern(second=4), legacy(last=usage(), second=5),
                 event("event_msg", dict(type="task_complete", turn_id="turn-1"), 6)])
    prices, queries = PricingCatalog(tmp_path / "prices"), UsageQueries(store.path)
    with monkeypatch.context() as old:
        old.setattr(module, "PARSER_VERSION", 6)
        old.setattr(module, "_user_preview", lambda content: module._plain(content, 600))
        collector.scan(root)
        queries.rebuild(prices)
        before = queries.page()["rows"][0]
        before_calls = store.records()
        assert "SKILL.md" in before_calls[0]["prompt_preview"]
    collector.scan(root)
    queries.rebuild(prices)
    after = queries.page()["rows"][0]
    assert after["prompt_preview"] == "@Impeccable 调整图片里的布局。\n[image] x 1"
    assert store.records()[0]["prompt_preview"] == after["prompt_preview"]
    assert after["call_count"] == before["call_count"] == 1
    for key in ("id", "total_tokens", "input_tokens", "cached_input_tokens", "output_tokens", "cost_usd"):
        assert after[key] == before[key]
    assert {key: value for key, value in store.records()[0].items() if key != "prompt_preview"} == {
        key: value for key, value in before_calls[0].items() if key != "prompt_preview"}
    assert collector.scan(root)["bytes_read"] == 0


def test_task_rename_updates_queries_without_rescan(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern()])
    collector.scan(root)
    (root / "session_index.jsonl").write_text(json.dumps({"id": PARENT, "thread_name": "Renamed"}) + "\n", encoding="utf-8")
    assert collector.scan(root)["changed"] == 1
    assert store.records()[0]["session_title"] == "Renamed"


def test_reset_then_other_lane_cumulative_does_not_invent_large_spend(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), legacy(usage(1000, 400, 100), usage()),
                 legacy(usage(5000, 2000, 300), usage(), second=4, lane="other"),
                 legacy(usage(50, 0, 5), usage(50, 0, 5), second=5),
                 legacy(usage(5000, 2000, 300), None, second=6, lane="other")])
    collector.scan(root)
    assert sum(r["input_tokens"] for r in store.records()) == 250


def test_reparse_reconciles_replaced_legacy_without_old_records(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), legacy(usage(), usage())])
    collector.scan(root)
    old = store.records()[0]["id"]
    write(root, [meta(), turn(), legacy(usage(200, 80, 20), usage(200, 80, 20))])
    collector.scan(root)
    assert store.count_records() == 1
    assert store.records()[0]["input_tokens"] == 200
    assert old not in {row["id"] for row in store.records()}


def test_interrupted_reparse_does_not_delete_old_rows_and_resume_has_no_loss(tmp_path, monkeypatch):
    from codexio import usage_collector as module
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern("one"), modern("two", second=4)])
    collector.scan(root)
    write(root, [meta(), turn(), modern("one"), modern("three", second=5)])
    stop = threading.Event()
    original = module.process_entry
    def wrapped(entry, *args, **kwargs):
        result = original(entry, *args, **kwargs)
        if entry.get("type") == "token_usage_record":
            stop.set()
        return result
    monkeypatch.setattr(module, "process_entry", wrapped)
    status = collector.scan(root, stop=stop)
    assert status["cancelled"] and status["indexed_files"] == 0 and status["status"] == "error"
    assert {r["response_id"] for r in store.records()} == {"one", "two"}
    monkeypatch.setattr(module, "process_entry", original)
    collector.scan(root)
    assert {r["response_id"] for r in store.records()} == {"one", "three"}


def test_reconcile_preserves_a_response_still_present_on_another_source(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern()])
    collector.scan(root)
    store.upsert_records(store.records(), "ssh:backup")
    write(root, [meta(), turn()])
    collector.scan(root)
    assert store.count_records() == 1
    assert store.count_records(source_id="local") == 0
    assert store.count_records(source_id="ssh:backup") == 1
    assert store.records()[0]["source_id"] == "ssh:backup"


def test_remote_frames_reconcile_atomically_with_cursors(tmp_path):
    root, store, _collector = setup(tmp_path)
    write(root, [meta(), turn(), legacy(usage(), usage())])
    frames = scan_directory(root, source_id="ssh:test")
    store.import_frames(frames, "ssh:test", "ssh-cursor")
    write(root, [meta(), turn(), legacy(usage(200, 80, 20), usage(200, 80, 20))])
    next_frames = scan_directory(root, store.get_meta("ssh-cursor"), source_id="ssh:test")
    store.import_frames(next_frames, "ssh:test", "ssh-cursor")
    assert store.count_records() == 1 and store.records()[0]["input_tokens"] == 200
    assert store.get_meta("ssh-cursor") == next_frames["cursors"]


def test_modern_context_is_owned_by_response_thread_not_copy_container(tmp_path):
    root, store, collector = setup(tmp_path)
    # Child sorts first by date/name-independent UUID position here; either
    # import order must converge on the original model and preview.
    write(root, [meta(CHILD), turn(model="child-model"),
                 event("event_msg", {"type": "user_message", "message": "child private prompt"}),
                 modern(ident=PARENT, second=9)], ident=CHILD)
    write(root, [meta(), turn(model="parent-model"),
                 event("event_msg", {"type": "user_message", "message": "parent prompt"}), modern()])
    collector.scan(root)
    row = store.records()[0]
    assert store.count_records() == 1
    assert row["model"] == "parent-model" and row["prompt_preview"] == "parent prompt"
    assert row["timestamp"] == "2026-09-07T00:00:02.000Z"


def test_response_limit_only_comes_from_its_pair_not_later_refresh(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern("one"), legacy(usage(), usage(), lane="first"),
                 legacy(usage(), usage(), second=4, lane="refresh-only"),
                 modern("two", second=5), legacy(usage(200, 80, 20), usage(), second=6, lane="second")])
    collector.scan(root)
    assert {r["response_id"]: r["limit_id"] for r in store.records()} == {"one": "first", "two": "second"}


def test_quota_observations_are_isolated_by_source_and_reconciled(tmp_path):
    root, store, collector = setup(tmp_path)
    limit = {"used_percent": 12, "window_minutes": 10080, "resets_at": 1790000000}
    write(root, [meta(), turn(), legacy(usage(), usage(), secondary=limit)])
    collector.scan(root)
    store.upsert_observations(store.observations(), "ssh:other")
    assert len(store.observations()) == 2
    write(root, [meta(), turn()])
    collector.scan(root)
    assert len(store.observations()) == 1 and store.observations()[0]["source_id"] == "ssh:other"


def test_malformed_reparse_does_not_remove_previously_verified_usage(tmp_path):
    root, store, collector = setup(tmp_path)
    path = write(root, [meta(), turn(), modern()])
    collector.scan(root)
    write(root, [meta(), turn()])
    with path.open("ab") as stream:
        stream.write(b'{"type":"token_usage_record", bad-json}\n')
    status = collector.scan(root)
    assert status["status"] == "error"
    assert store.count_records() == 1


def test_reindex_clears_and_reimports_stable_identities(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern()])
    collector.scan(root)
    identity = store.records()[0]["id"]
    store.clear_index()
    assert store.records() == []
    collector.scan(root)
    assert [row["id"] for row in store.records()] == [identity]


def test_same_length_middle_rewrite_is_detected_even_if_head_tail_unchanged(tmp_path):
    root, store, collector = setup(tmp_path)
    padding = event("response_item", {"role": "assistant", "content": "x" * 2000})
    path = write(root, [meta(), turn(), padding, legacy(usage(), usage()), padding])
    collector.scan(root)
    previous_stat = path.stat()
    content = path.read_bytes()
    changed = content.replace(b'"input_tokens": 100', b'"input_tokens": 200')
    assert len(changed) == len(content) and changed[:1024] == content[:1024] and changed[-1024:] == content[-1024:]
    path.write_bytes(changed)
    import os
    os.utime(path, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns + 1_000_000))
    collector.scan(root)
    assert store.count_records() == 1 and store.records()[0]["input_tokens"] == 200


def assistant(text="Visible answer", second=2, **fields):
    return event("response_item", dict(type="message", role="assistant", phase="final_answer",
                                      content=[dict(type="output_text", text=text)], **fields), second)


def test_modern_output_preview_belongs_to_its_response_only(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern("tool-response"), legacy(usage(), usage()),
                 assistant("Final answer", second=4), modern("final-response", second=5),
                 legacy(usage(200, 80, 20), usage(), second=6)])
    collector.scan(root)
    rows = {r["response_id"]: r for r in store.records()}
    assert rows["tool-response"]["output_preview"] == ""
    assert rows["final-response"]["output_preview"] == "Final answer"
    assert sum(r["total_tokens"] for r in rows.values()) == 220


def test_explicit_output_before_and_after_meter_and_incremental_backfill(tmp_path):
    root, store, collector = setup(tmp_path)
    path = write(root, [meta(), turn(), assistant("First", response_id="one"), modern("one"),
                        modern("two", second=4), legacy(usage(200, 80, 20), usage(), second=5)])
    collector.scan(root)
    append(path, [assistant("Second", second=6, response_id="two"),
                  assistant("First", second=7, response_id="one")])
    collector.scan(root)
    assert {r["response_id"]: r["output_preview"] for r in store.records()} == {"one": "First", "two": "Second"}
    assert store.count_records() == 2
    assert sum(r["total_tokens"] for r in store.records()) == 220
    assert {r["response_id"]: r["limit_id"] for r in store.records()}["two"] == "codex"
    assert collector.scan(root)["bytes_read"] == 0


def test_late_output_can_match_explicit_response_across_turn_boundary(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern("one"), turn("turn-2"),
                 assistant("First response", response_id="one"),
                 modern("two", second=5, turn_id="turn-2")])
    collector.scan(root)
    assert {r["response_id"]: r["output_preview"] for r in store.records()} == {"one": "First response", "two": ""}


def test_unknown_output_after_meter_without_identity_is_not_guessed(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern("one"), legacy(usage(), usage()),
                 assistant("No matching accounting record", second=4),
                 event("event_msg", dict(type="task_complete", last_agent_message="Do not copy this"), 5),
                 turn("turn-2"), modern("two", second=6, turn_id="turn-2")])
    collector.scan(root)
    assert all(r["output_preview"] == "" for r in store.records())


def test_explicit_completion_boundary_attaches_late_message(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), modern("one"), assistant("After accounting"),
                 event("event_msg", dict(type="raw_response_completed", response_id="one"), 3)])
    collector.scan(root)
    assert store.count_records() == 1
    assert store.records()[0]["output_preview"] == "After accounting"


def test_legacy_visible_output_deduplicates_event_and_response_item(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), event("event_msg", dict(type="agent_message", phase="final_answer", message="Legacy answer")),
                 assistant("Legacy answer"), legacy(usage(), usage())])
    collector.scan(root)
    assert store.records()[0]["output_preview"] == "Legacy answer"
    assert store.records()[0]["quality"] == "legacy_last"


def test_legacy_output_after_meter_requires_explicit_response_id(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), event("event_msg", dict(type="raw_response_completed", response_id="old")),
                 legacy(usage(), usage()), assistant("Legacy late", response_id="old", second=4)])
    collector.scan(root)
    assert store.count_records() == 1
    assert store.records()[0]["output_preview"] == "Legacy late"


def test_previews_exclude_reasoning_tools_hidden_channels_and_foreign_owners(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(),
                 event("response_item", dict(type="reasoning", role="assistant", content="REASONING_SECRET")),
                 event("response_item", dict(type="function_call", role="assistant", content="CALL_SECRET")),
                 event("response_item", dict(type="message", role="tool", content="TOOL_SECRET")),
                 event("event_msg", dict(type="item_completed", item=dict(type="Reasoning", summary_text="SUMMARY_SECRET"))),
                 event("response_item", dict(type="message", role="assistant", channel="analysis", content="CHANNEL_SECRET")),
                 event("response_item", dict(type="message", role="assistant", recipient="functions.tool", content="RECIPIENT_SECRET")),
                 assistant("FOREIGN_SECRET", thread_id=CHILD),
                 event("response_item", dict(type="message", role="assistant", content=[
                     dict(type="reasoning_text", text="REASONING_PART_SECRET"), dict(type="output_text", text="Visible")])), modern()])
    collector.scan(root)
    row = store.records()[0]
    assert row["output_preview"] == "Visible"
    assert "SECRET" not in json.dumps(row)
    cursor = store.get_cursor(cursor_key(root, next((root / "sessions").rglob("*.jsonl")), "local"))
    assert "SECRET" not in json.dumps(cursor)


def test_preview_limits_and_copied_context_preserve_owner_output(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), event("event_msg", dict(type="user_message", message="u" * 800)),
                 assistant("a" * 800), modern()])
    collector.scan(root)
    row = store.records()[0]
    assert len(row["prompt_preview"]) == len(row["output_preview"]) == 600
    copied = dict(row, prompt_preview="wrong user", output_preview="wrong output", context_owner_verified=False)
    store.upsert_records([copied], "ssh:copy")
    assert store.records()[0]["output_preview"] == "a" * 600
    assert store.records()[0]["prompt_preview"] == row["prompt_preview"]
    assert store.count_records() == 1


def test_parser_upgrade_reloads_old_cursor_and_backfills_preview_without_recount(tmp_path):
    from codexio import usage_collector as module
    root, store, collector = setup(tmp_path)
    path = write(root, [meta(), turn(), assistant("Backfilled answer"), modern()])
    collector.scan(root)
    key = cursor_key(root, path, "local")
    cursor = store.get_cursor(key)
    cursor["version"] = module.PARSER_VERSION - 1
    cursor["state"].pop("preview_pending", None)
    cursor["state"].pop("preview_recent", None)
    store.set_cursor(key, cursor)
    with store._connect() as db:
        row = store.records()[0]
        row.pop("output_preview")
        db.execute("UPDATE usage_records SET data=? WHERE id=?", (json.dumps(row), row["id"]))
    status = collector.scan(root)
    assert status["bytes_read"] == path.stat().st_size
    assert store.count_records() == 1 and store.records()[0]["total_tokens"] == 110
    assert store.records()[0]["output_preview"] == "Backfilled answer"
    assert store.get_cursor(key)["version"] == module.PARSER_VERSION


def test_inherited_legacy_answer_does_not_leak_into_child_request(tmp_path):
    root, store, collector = setup(tmp_path)
    write(root, [meta(), turn(), assistant("Parent answer"), legacy(usage(), usage())])
    child_meta = meta(CHILD, forked_from_id=PARENT)
    child_meta["timestamp"] = "2026-09-07T00:00:10Z"
    write(root, [child_meta, turn(), assistant("Copied parent answer"),
                 legacy(usage(), usage(), second=11),
                 legacy(usage(200, 80, 20), usage(), second=12)], ident=CHILD)
    collector.scan(root)
    assert {r["session_id"]: r["output_preview"] for r in store.records()} == {PARENT: "Parent answer", CHILD: ""}


def test_invalid_modern_meter_does_not_shift_output_to_next_request(tmp_path):
    root, store, collector = setup(tmp_path)
    invalid = modern("invalid")
    invalid["payload"]["usage"] = {"total_tokens": 123}
    write(root, [meta(), turn(), assistant("Unaccounted answer"), invalid, modern("next", second=4)])
    collector.scan(root)
    assert store.count_records() == 1
    assert store.records()[0]["output_preview"] == ""
