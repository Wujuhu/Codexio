from __future__ import annotations

import json
import subprocess
import sys

import pytest

from codexio.remote_collector import _remote_script, build_ssh_command, collect_ssh


@pytest.mark.parametrize("host", ["-oProxyCommand=bad", "host; touch nope", "$(whoami)", "a b", "a\nwhoami", "x'", "", "a/b"])
def test_ssh_rejects_argument_or_shell_injection(host):
    with pytest.raises(ValueError):
        build_ssh_command({"host": host})


def test_ssh_uses_existing_authentication_and_hidden_subprocess(monkeypatch):
    seen = {}
    def fake(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 0, b'{"records":[],"observations":[],"cursors":{}}', b"")
    monkeypatch.setattr(subprocess, "run", fake)
    assert collect_ssh({"host": "user@my-config-alias"}, {})["records"] == []
    assert "BatchMode=yes" in seen["command"]
    assert seen["command"][-1] == "python3 -"
    assert seen["kwargs"]["shell"] is False
    assert isinstance(seen["kwargs"]["input"], bytes)


def test_embedded_scanner_is_standalone_and_paths_never_become_shell_code(tmp_path):
    root = tmp_path / "directory ' with $(quotes)"
    root.mkdir()
    source = {"host": "server", "root": str(root)}
    script = _remote_script(source, {})
    result = subprocess.run([sys.executable, "-"], input=script.encode("utf-8"), capture_output=True, check=True)
    assert json.loads(result.stdout)["records"] == []
    assert not list(root.iterdir())


def test_remote_error_does_not_echo_private_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, b"", b"PRIVATE_TOKEN"))
    with pytest.raises(RuntimeError) as error:
        collect_ssh({"host": "server"}, {})
    assert "PRIVATE_TOKEN" not in str(error.value)


def test_remote_python_path_is_quoted_by_validation():
    assert build_ssh_command({"host": "server", "python": "/usr/bin/python3"})[-1] == "/usr/bin/python3 -"
    with pytest.raises(ValueError):
        build_ssh_command({"host": "server", "python": "python3; touch nope"})


def test_embedded_scanner_returns_bounded_visible_output_and_incremental_backfill(tmp_path):
    from codexio.usage_store import UsageStore
    directory = tmp_path / "sessions"
    directory.mkdir()
    path = directory / "rollout-11111111-1111-4111-8111-111111111111.jsonl"
    entries = [
        dict(type="session_meta", payload=dict(id="session-one")),
        dict(type="turn_context", payload=dict(turn_id="turn-one", model="test")),
        dict(type="token_usage_record", timestamp="2026-09-07T00:00:00Z", payload=dict(
            thread_id="session-one", turn_id="turn-one", response_id="response-one",
            usage=dict(input_tokens=100, output_tokens=20))),
    ]
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    source = {"host": "server", "id": "ssh:test", "root": str(tmp_path)}

    def run(cursors):
        script = _remote_script(source, cursors)
        result = subprocess.run([sys.executable, "-"], input=script.encode("utf-8"), capture_output=True, check=True)
        return json.loads(result.stdout)

    frames = run({})
    store = UsageStore(tmp_path / "import.sqlite")
    store.import_frames(frames, "ssh:test", "ssh-cursor")
    late = dict(type="response_item", timestamp="2026-09-07T00:00:01Z", payload=dict(
        type="message", role="assistant", response_id="response-one", content=[
            dict(type="reasoning_text", text="HIDDEN_SECRET"), dict(type="output_text", text="x" * 900)]))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(late) + "\n")
    updates = run(store.get_meta("ssh-cursor"))
    store.import_frames(updates, "ssh:test", "ssh-cursor")
    row = store.records()[0]
    assert store.count_records() == 1 and row["total_tokens"] == 120
    assert row["output_preview"] == "x" * 600
    assert "HIDDEN_SECRET" not in json.dumps(updates)
    assert not run(store.get_meta("ssh-cursor"))["records"]
