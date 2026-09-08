from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiquota.app_server import MessageKind, parse_jsonrpc_line
from aiquota.logging_setup import redact_text
from aiquota.rate_limits import parse_rate_limits_result

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8").strip()


def test_parse_initialize_response() -> None:
    message = parse_jsonrpc_line(_load_text("initialize_response.json"))
    assert message.kind == MessageKind.RESPONSE
    assert message.id == 1
    assert message.result["platformOs"] == "windows"


def test_parse_rate_limits_response() -> None:
    raw = json.loads(_load_text("rate_limits_read.json"))
    message = parse_jsonrpc_line(json.dumps(raw))
    assert message.kind == MessageKind.RESPONSE
    snapshot = parse_rate_limits_result(message.result)
    assert snapshot.primary is not None
    assert snapshot.primary.window_duration_mins == 300


def test_parse_rate_limits_notification() -> None:
    message = parse_jsonrpc_line(_load_text("rate_limits_updated.json"))
    assert message.kind == MessageKind.NOTIFICATION
    assert message.method == "account/rateLimits/updated"
    assert message.id is None
    assert message.params["rateLimits"]["primary"]["usedPercent"] == 31


def test_parse_error_response() -> None:
    message = parse_jsonrpc_line(_load_text("error_auth.json"))
    assert message.kind == MessageKind.ERROR
    assert message.id == 7
    assert "authentication" in (message.error or {})["message"]


def test_parse_server_initiated_request() -> None:
    message = parse_jsonrpc_line(
        '{"method":"item/commandExecution/requestApproval","id":"req-1","params":{}}'
    )
    assert message.kind == MessageKind.REQUEST
    assert message.id == "req-1"


def test_invalid_json_and_empty_lines() -> None:
    lines = (FIXTURES / "invalid_lines.txt").read_text(encoding="utf-8").splitlines()
    with pytest.raises(ValueError, match="empty"):
        parse_jsonrpc_line(lines[0])
    with pytest.raises(ValueError, match="invalid JSON"):
        parse_jsonrpc_line(lines[1])
    with pytest.raises(ValueError, match="object"):
        parse_jsonrpc_line(lines[3])
    valid = parse_jsonrpc_line(lines[4])
    assert valid.kind == MessageKind.NOTIFICATION


def test_unrecognized_object_is_rejected() -> None:
    with pytest.raises(ValueError, match="unrecognized"):
        parse_jsonrpc_line('{"only":"object","but":"not-rpc"}')


def test_redact_tokens_from_logs() -> None:
    leaked = '{"access_token":"sk-secret","refresh_token":"abc","note":"ok"} Authorization: Bearer xyz'
    redacted = redact_text(leaked)
    assert "sk-secret" not in redacted
    assert "abc" not in redacted
    assert "xyz" not in redacted
    assert "[redacted]" in redacted
    assert "ok" in redacted
