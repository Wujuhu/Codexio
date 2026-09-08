from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from aiquota.analytics_config import default_config
from aiquota.pricing import PricingCatalog
from aiquota.usage_worker import UsageWorker
from aiquota.usage_queries import UsageQueries


def setup_worker(tmp_path, monkeypatch, *, records=(), scan_error=False, **config):
    settings = dict(default_config(), codex_roots=[str(tmp_path / "codex")],
                    auto_sync_prices=False,
                    account_since=(datetime.now(timezone.utc) - timedelta(days=35)).isoformat())
    settings.update(config)
    worker = UsageWorker(settings, directory=tmp_path / "application")
    events = []
    worker.loading_changed.connect(lambda state: events.append(("loading", state)))
    worker.data_changed.connect(lambda data: events.append(("data", data)))

    class FakeCollector:
        def __init__(self, store):
            self.store = store

        def scan(self, root, *, source_id, **_kwargs):
            assert events[-1][1]["stage"] == "正在扫描本机日志" or not worker._initial_loading
            if scan_error:
                raise OSError("private file path")
            if records:
                self.store.upsert_records(records, source_id)
            self.store.set_source_status(source_id, status="ok", name="本机")
            return {"changed": bool(records), "indexed_files": len(records)}

    monkeypatch.setattr("aiquota.usage_collector.Collector", FakeCollector)
    monkeypatch.setattr(worker, "_collect_remote", lambda: None)
    monkeypatch.setattr(PricingCatalog, "sync", lambda *_args, **_kwargs: pytest.fail("unexpected network sync"))
    worker._wake = SimpleNamespace(clear=lambda: None, set=lambda: None,
                                   wait=lambda *_args: worker._stop_event.set())
    return worker, events


def example_record():
    return {
        "id": "sample", "session_id": "session", "source_id": "local", "quality": "response",
        "timestamp": datetime.now(timezone.utc).isoformat(), "model": "gpt-6-astra",
        "provider": "openai", "service_tier": "default", "input_tokens": 1000,
        "cached_input_tokens": 200, "output_tokens": 100, "total_tokens": 1100,
    }


@pytest.mark.parametrize("records", [[], [example_record()]])
def test_first_load_finishes_after_initial_data_including_empty_history(tmp_path, monkeypatch, records):
    worker, events = setup_worker(tmp_path, monkeypatch, records=records)
    worker.run()
    states = [value for kind, value in events if kind == "loading"]
    assert states[0] == {"loading": True, "stage": "正在准备本地用量数据", "error": None}
    assert [s["stage"] for s in states] == ["正在准备本地用量数据", "正在扫描本机日志",
                                               "正在汇总用量数据", "用量数据已加载"]
    assert states[-1] == {"loading": False, "stage": "用量数据已加载", "error": None}
    assert events[-2][0] == "data" and events[-1][0] == "loading"
    assert UsageQueries(events[-2][1]["query_path"]).page("model_call")["total"] == len(records)
    assert "records" not in events[-2][1]


def test_loading_starts_before_store_initialization_and_ends_on_safe_failure(tmp_path, monkeypatch):
    worker, events = setup_worker(tmp_path, monkeypatch)

    def fail_store(_path):
        assert events == [("loading", {"loading": True, "stage": "正在准备本地用量数据", "error": None})]
        raise PermissionError("private path and credentials must not reach the UI")

    monkeypatch.setattr("aiquota.usage_store.UsageStore", fail_store)
    worker.run()
    assert len(events) == 2
    assert events[-1][1]["loading"] is False
    assert events[-1][1]["stage"] == "用量数据加载失败"
    assert "PermissionError" in events[-1][1]["error"]
    assert "private" not in events[-1][1]["error"]


def test_initial_aggregation_failure_closes_loading_without_publishing(tmp_path, monkeypatch):
    worker, events = setup_worker(tmp_path, monkeypatch, records=[example_record()])

    def fail_price(*_args):
        raise RuntimeError("private record")

    monkeypatch.setattr(PricingCatalog, "price", fail_price)
    worker.run()
    assert not any(kind == "data" for kind, _value in events)
    assert events[-1][1]["loading"] is False
    assert "RuntimeError" in events[-1][1]["error"]


def test_first_scan_failure_publishes_source_error_and_finishes_loading(tmp_path, monkeypatch):
    worker, events = setup_worker(tmp_path, monkeypatch, scan_error=True)
    worker.run()
    data = next(value for kind, value in events if kind == "data")
    assert data["sources"][0]["status"] == "error"
    assert data["sources_complete"] is False
    assert events[-1][1] == {"loading": False, "stage": "用量数据已加载", "error": None}


def test_remote_stage_stays_loading_until_first_complete_publication(tmp_path, monkeypatch):
    worker, events = setup_worker(tmp_path, monkeypatch,
                                  ssh_sources=[{"id": "peer", "host": "peer", "enabled": True}])

    def collect_remote():
        assert events[-1] == ("loading", {"loading": True, "stage": "正在同步远程用量", "error": None})
        assert not any(kind == "data" for kind, _value in events)
        worker._store.set_source_status("ssh:peer", status="ok", name="peer")

    monkeypatch.setattr(worker, "_collect_remote", collect_remote)
    worker.run()
    assert events[-2][1]["sources_complete"] is True
    assert events[-1][1]["loading"] is False


def test_refresh_and_price_sync_do_not_restart_initial_loading(tmp_path, monkeypatch):
    worker, events = setup_worker(tmp_path, monkeypatch)
    waits, syncs = [], []
    monkeypatch.setattr(PricingCatalog, "sync", lambda *_args, **_kwargs: syncs.append(True))

    def next_iteration(_timeout):
        waits.append(True)
        if len(waits) == 1:
            worker.request_refresh()
            worker.request_sync()
        elif len(waits) == 3:
            worker.stop()

    worker._wake.wait = next_iteration
    worker.run()
    finished = next(index for index, (kind, value) in enumerate(events)
                    if kind == "loading" and value["loading"] is False)
    assert syncs == [True]
    assert sum(kind == "data" for kind, _value in events) == 3
    assert all(kind != "loading" for kind, _value in events[finished + 1:])


def test_stop_before_first_publication_closes_loading_as_cancelled(tmp_path, monkeypatch):
    worker, events = setup_worker(tmp_path, monkeypatch)

    def stop_during_scan(_collector):
        worker.stop()

    monkeypatch.setattr(worker, "_run_loop", stop_during_scan)
    worker.run()
    assert events[-1] == ("loading", {"loading": False, "stage": "已停止加载", "error": None})
    assert not any(kind == "data" for kind, _value in events)


def test_mock_first_load_has_same_completion_lifecycle_without_network(tmp_path, monkeypatch):
    worker, events = setup_worker(tmp_path, monkeypatch)
    worker._mock = True
    worker.run()
    assert events[-2][0] == "data"
    assert UsageQueries(events[-2][1]["query_path"]).page("model_call")["total"] == 420
    assert events[-1] == ("loading", {"loading": False, "stage": "用量数据已加载", "error": None})


def test_invalid_utf8_quota_cache_does_not_abort_initial_quota_loading(tmp_path):
    from aiquota.settings import load_quota_cache
    path = tmp_path / "quota_cache.json"
    path.write_bytes(b"\xff\xfeinvalid")
    assert load_quota_cache(path) == {}
