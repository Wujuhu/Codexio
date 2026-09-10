import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from codexio.server_usage import (Credentials, ServerUsageClient, ServerUsageError, _NoRedirect,
                                   parse_daily, parse_quota, read_credentials)
from codexio.server_usage_store import ServerUsageStore, read_server_snapshot
from codexio.server_usage_monitor import ServerUsageMonitor
from codexio.analytics_config import default_config, load_analytics_config, save_analytics_config

NOW = datetime.now(timezone.utc)
TOKEN = "header." + base64.urlsafe_b64encode(json.dumps({"sub": "private-user-id"}).encode()).decode().rstrip("=") + ".signature"
CREDS = Credentials(TOKEN, "private-account-id", "private-user-id")


def quota_payload(**changes):
    result = dict(account_id=CREDS.account_id, plan_type="pro", rate_limit={
        "primary_window": dict(used_percent=30, limit_window_seconds=604800, reset_at=int((NOW+timedelta(days=3)).timestamp())),
        "secondary_window": None}, additional_rate_limits=[{"metered_feature": "codex_bengalfox"}])
    result.update(changes)
    return result


def daily_payload():
    return dict(balance_unit="credit", group_by="day", data=[dict(date=(NOW-timedelta(days=3)).date().isoformat(),
        totals=dict(credits=1234.567, uncached_text_input_tokens=1, cached_text_input_tokens=2, text_output_tokens=3,
                    text_total_tokens=6), models=[dict(model="gpt-6-astra", credits=0, turns=4)])])


def parsed_daily(payload=None):
    return parse_daily(payload or daily_payload(), CREDS, (NOW-timedelta(days=10)).date(), NOW.date(), NOW)


def test_parser_keeps_only_numeric_account_scoped_totals():
    quota = parse_quota(quota_payload(), CREDS, started=NOW, finished=NOW)
    day, = parsed_daily()
    assert quota["limit_id"] == "codex" and quota["used_percent"] == 30
    assert day["credits"] == 1234.567 and day["scope_ok"]
    serialized = json.dumps([quota, day])
    assert CREDS.token not in serialized and CREDS.account_id not in serialized
    assert CREDS.token not in repr(CREDS) and CREDS.account_id not in repr(CREDS)
    assert CREDS.subject not in serialized and CREDS.subject not in repr(CREDS)


def test_shared_workspace_members_have_separate_ledgers():
    other = Credentials(CREDS.token, CREDS.account_id, "second-member")
    assert CREDS.key != other.key
    refreshed = Credentials("refreshed-token", CREDS.account_id, CREDS.subject)
    assert CREDS.key == refreshed.key


@pytest.mark.parametrize("change,code", [({"account_id": "other"}, "account_mismatch"),
                                       ({"rate_limit": {}}, "no_weekly_quota")])
def test_quota_account_must_match_and_weekly_window_must_exist(change, code):
    with pytest.raises(ServerUsageError) as error:
        parse_quota(quota_payload(**change), CREDS, started=NOW, finished=NOW)
    assert error.value.code == code


@pytest.mark.parametrize("bad", [True, -1, float("nan"), float("inf"), "invalid"])
def test_invalid_daily_credits_are_rejected(bad):
    data = daily_payload()
    data["data"][0]["totals"]["credits"] = bad
    with pytest.raises(ServerUsageError):
        parsed_daily(data)


@pytest.mark.parametrize("case", ["duplicate", "sum", "unit", "scope", "future", "account"])
def test_changed_schema_dates_duplicates_counts_and_identity_are_checked(case):
    data = daily_payload()
    if case == "duplicate":
        data["data"] *= 2
    elif case == "sum":
        data["data"][0]["totals"]["text_total_tokens"] = 9
    elif case == "unit":
        data["balance_unit"] = "percent"
    elif case == "scope":
        data["group_by"] = "model"
    elif case == "future":
        data["data"][0]["date"] = (NOW+timedelta(days=1)).date().isoformat()
    else:
        data["account_id"] = "other"
    with pytest.raises(ServerUsageError):
        parsed_daily(data)


def test_spark_activity_blocks_day_even_when_model_credit_breakdown_is_zero():
    data = daily_payload()
    data["data"][0]["models"].append(dict(model="gpt-5.3-codex-spark", credits=0, turns=1))
    assert not parsed_daily(data)[0]["scope_ok"]


def test_missing_day_stays_absent_instead_of_becoming_zero():
    data = daily_payload()
    data["data"] = []
    assert parsed_daily(data) == []


def test_credentials_read_utf8_local_tokens_and_fail_without_exposing_contents(tmp_path):
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": {"access_token": CREDS.token, "account_id": CREDS.account_id}}), encoding="utf-8")
    assert read_credentials(tmp_path) == CREDS
    (tmp_path / "auth.json").write_text("secret-malformed-内容", encoding="utf-8")
    with pytest.raises(ServerUsageError) as error:
        read_credentials(tmp_path)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("status,code", [(401, "auth_expired"), (403, "auth_expired"), (429, "rate_limited"),
                                        (404, "unavailable"), (302, "unavailable")])
def test_http_errors_do_not_include_tokens_or_response_details(status, code):
    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith("https://chatgpt.com/backend-api/wham/")
            assert timeout == 12
            raise HTTPError(request.full_url, status, "secret-token and private-account-id", {}, None)
    with pytest.raises(ServerUsageError) as error:
        ServerUsageClient(Opener()).quota(CREDS)
    assert error.value.code == code
    assert "secret" not in str(error.value) and "private-account" not in str(error.value)
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test") is None


def test_independent_ledger_replaces_backfill_and_isolates_accounts(tmp_path):
    path = tmp_path / "server_usage.sqlite"
    assert not read_server_snapshot(path)["daily"] and not path.exists()
    store = ServerUsageStore(path)
    quota = parse_quota(quota_payload(), CREDS, started=NOW, finished=NOW)
    days = parsed_daily()
    context = dict(account_key=CREDS.key, quota_status="ok")
    store.save(context, observation=quota, daily=days)
    store.save(context, observation=quota, daily=[dict(days[0], credits=999)])
    saved = read_server_snapshot(path)
    assert len(saved["observations"]) == 1 and saved["daily"][0]["credits"] == 999
    store.save(dict(account_key="other-account", quota_status="ok"))
    assert read_server_snapshot(path)["daily"] == []
    assert store.account_context(CREDS.key) == context
    store.save(context, daily=[], date_range=(days[0]["date"], days[0]["date"]))
    assert not read_server_snapshot(path)["daily"]
    store.save(dict(quota_status="auth_missing", account_key=None))
    assert read_server_snapshot(path)["context"]["quota_status"] == "auth_missing"
    assert not read_server_snapshot(path)["observations"]
    assert CREDS.token.encode() not in path.read_bytes() and CREDS.account_id.encode() not in path.read_bytes()


def test_store_rejects_mixed_account_transaction(tmp_path):
    store = ServerUsageStore(tmp_path / "server.sqlite")
    with pytest.raises(ValueError):
        store.save(dict(account_key="other"), daily=parsed_daily())
    assert not read_server_snapshot(store.path)["daily"]


def test_monitor_collects_startup_history_and_reuses_daily_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("codexio.server_usage_monitor.read_credentials", lambda: CREDS)
    class Client:
        daily_calls = 0
        def quota(self, credentials):
            return parse_quota(quota_payload(), credentials, started=NOW, finished=NOW)
        def daily(self, credentials, start, end):
            self.daily_calls += 1
            assert (end - start).days == 89
            return parsed_daily()
    client = Client()
    monitor = ServerUsageMonitor(default_config(), tmp_path, client=client)
    monitor._store = ServerUsageStore(monitor._path)
    monitor.collect_once()
    monitor.collect_once()
    assert client.daily_calls == 1
    assert read_server_snapshot(monitor._path)["context"]["daily_status"] == "ok"
    monitor.request_refresh()
    monitor.collect_once()
    assert client.daily_calls == 2
    monitor.stop()


def test_mock_and_disabled_monitor_do_not_start_network_requests(tmp_path):
    class Never:
        def quota(self, credentials):
            pytest.fail("unexpected network")
    monitor = ServerUsageMonitor(default_config(), tmp_path, mock=True, client=Never())
    monitor.start()
    assert monitor._thread is None and not monitor._path.exists()
    monitor = ServerUsageMonitor(dict(default_config(), server_estimates_enabled=False), tmp_path, client=Never())
    monitor.start()
    monitor.stop()
    assert not monitor._path.exists()


def test_daily_failure_keeps_existing_cache_and_new_quota_samples(tmp_path, monkeypatch):
    monkeypatch.setattr("codexio.server_usage_monitor.read_credentials", lambda: CREDS)
    class Client:
        def quota(self, credentials):
            return parse_quota(quota_payload(), credentials, started=NOW, finished=NOW)
        def daily(self, *args):
            raise ServerUsageError("rate_limited")
    monitor = ServerUsageMonitor(default_config(), tmp_path, client=Client())
    monitor._store = ServerUsageStore(monitor._path)
    monitor._store.save(dict(account_key=CREDS.key, quota_status="ok", last_daily_at=(NOW-timedelta(days=1)).isoformat()), daily=parsed_daily())
    monitor.collect_once()
    saved = read_server_snapshot(monitor._path)
    assert len(saved["daily"]) == len(saved["observations"]) == 1
    assert saved["context"]["daily_status"] == "rate_limited"
    assert saved["context"]["quota_status"] == "ok"


def test_switching_user_does_not_return_the_previous_users_cached_data(tmp_path, monkeypatch):
    other = Credentials(TOKEN, CREDS.account_id, "second-member")
    class Client:
        def quota(self, credentials):
            raise ServerUsageError("network_error")
    monitor = ServerUsageMonitor(default_config(), tmp_path, client=Client())
    monitor._store = ServerUsageStore(monitor._path)
    monitor._store.save(dict(account_key=CREDS.key, quota_status="ok"), daily=parsed_daily())
    monkeypatch.setattr("codexio.server_usage_monitor.read_credentials", lambda: other)
    monitor.collect_once()
    saved = read_server_snapshot(monitor._path)
    assert saved["context"]["account_key"] == other.key and saved["daily"] == []
    def missing():
        raise ServerUsageError("auth_missing")
    monkeypatch.setattr("codexio.server_usage_monitor.read_credentials", missing)
    monitor.collect_once()
    assert read_server_snapshot(monitor._path)["context"]["account_key"] is None


def test_shutdown_during_request_does_not_write_or_emit_after_stop(tmp_path, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr("codexio.server_usage_monitor.read_credentials", lambda: CREDS)
    class Client:
        def quota(self, credentials):
            started.set()
            assert release.wait(5)
            return parse_quota(quota_payload(), credentials, started=NOW, finished=NOW)
        def daily(self, *args):
            pytest.fail("daily request after stop")
    monitor = ServerUsageMonitor(default_config(), tmp_path, client=Client())
    monitor.start()
    try:
        assert started.wait(3)
        monitor.stop()
    finally:
        release.set()
        monitor._thread.join(timeout=3)
    assert not monitor._thread.is_alive()
    assert read_server_snapshot(monitor._path)["observations"] == []


def test_credit_conversion_is_not_a_user_setting(tmp_path):
    path = tmp_path / "settings.json"
    save_analytics_config(dict(default_config(), usd_per_credit=0.025, server_estimates_enabled=False), path)
    data = load_analytics_config(path)
    assert "usd_per_credit" not in data and not data["server_estimates_enabled"]
    assert "usd_per_credit" not in path.read_text(encoding="utf-8")
