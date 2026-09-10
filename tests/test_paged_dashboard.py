from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from codexio.analytics_config import default_config
from codexio.dashboard import Dashboard, UserRequestDetails
from codexio.pricing import PricingCatalog
from codexio.settings import AppSettings
from codexio.usage_store import UsageStore
from codexio.usage_worker import UsageWorker


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def worker(tmp_path):
    config = dict(default_config(), auto_sync_prices=False, auto_update=False, codex_roots=[])
    result = UsageWorker(config, mock=True, directory=tmp_path)
    result._store = UsageStore(tmp_path / "usage.sqlite")
    result._catalog = PricingCatalog(tmp_path / "prices")
    result._seed_mock()
    return result


def publish(worker):
    snapshots = []
    worker.data_changed.connect(snapshots.append)
    worker._publish()
    worker.data_changed.disconnect(snapshots.append)
    return snapshots[-1]


def test_runtime_dashboard_keeps_only_one_page_and_opens_correct_second_page_row(app, worker):
    data = publish(worker)
    assert "records" not in data and "user_requests" not in data
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    window.apply_data(data)
    window._log_period.setCurrentIndex(window._log_period.findData("all"))
    assert window._query_page["total"] == 210
    assert window._log_table.rowCount() == len(window._filtered_records) == 100
    assert window._records == [] and window._records_by_id == {} and window._user_requests == []
    first_ids = {row["id"] for row in window._filtered_records}
    window._change_page(1)
    assert not first_ids.intersection(row["id"] for row in window._filtered_records)
    expected = window._filtered_records[0]
    window._show_request_row(0)
    dialog = window._dialogs[-1]
    assert isinstance(dialog, UserRequestDetails)
    assert dialog.session_id.text() == expected["session_id"]
    assert dialog.call_table.rowCount() == expected["call_count"]
    dialog.close()
    window._change_page(1)
    assert window._log_table.rowCount() == 10
    assert window._page_indicator.text() == "3 / 3"
    window.hide()
    window.deleteLater()


def test_database_filters_preserve_whole_request_and_raw_mode_totals(app, worker):
    data = publish(worker)
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    window.apply_data(data)
    window._log_period.setCurrentIndex(window._log_period.findData("all"))
    expected = window._filtered_records[0]
    window._log_model.setCurrentIndex(window._log_model.findData("gpt-6-astra"))
    actual = next(row for row in window._filtered_records if row["id"] == expected["id"])
    assert actual["cost_usd"] == expected["cost_usd"] and actual["call_count"] == 2
    assert window._log_model.itemText(0) == "全部模型"
    assert window._log_model.itemText(1) == "gpt-6-astra"
    window._log_model.setCurrentIndex(0)
    window._log_mode.setCurrentIndex(window._log_mode.findData("model_call"))
    assert window._query_page["total"] == 420
    assert window._log_table.columnCount() == 7
    assert len(window._filtered_records) == 100
    window._change_page(4)
    assert window._log_table.rowCount() == 20
    ids = [row["id"] for row in window._filtered_records]
    window.apply_data(publish(worker))
    assert window._page_number == 4
    assert [row["id"] for row in window._filtered_records] == ids
    window.open_page("overview")
    assert window._latest_record["record_kind"] == "user_request"
    assert window._overview_chart.buckets
    window.hide()
    window.deleteLater()


def test_large_request_details_page_calls_without_retaining_all_members(app, worker):
    worker._store.clear_index()
    now = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = [dict(id="large:%03d" % i, response_id="reply-%03d" % i, session_id="large-session", turn_id="large-turn",
                 timestamp=(now + timedelta(seconds=i)).isoformat(), model="gpt-6-astra", service_tier="default",
                 provider="openai", limit_id="codex", input_tokens=1000, output_tokens=100, total_tokens=1100,
                 quality="response", source_id="local") for i in range(250)]
    worker._store.upsert_records(rows, "local")
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs")
    window.apply_data(publish(worker))
    window._log_period.setCurrentIndex(window._log_period.findData("all"))
    assert window._query_page["total"] == 1
    window._show_request_row(0)
    dialog = window._dialogs[-1]
    assert len(dialog._members) == dialog.call_table.rowCount() == 100
    assert dialog._members[0]["response_id"] == "reply-000"
    dialog._next_member.click()
    assert dialog._members[0]["response_id"] == "reply-100"
    dialog._next_member.click()
    assert len(dialog._members) == dialog.call_table.rowCount() == 50
    assert dialog._members[0]["response_id"] == "reply-200"
    dialog.call_table.cellDoubleClicked.emit(0, 0)
    labels = [label.text() for label in dialog._children[-1].findChildren(QLabel)]
    assert "reply-200" in labels
    dialog._children[-1].close()
    assert not dialog._children
    dialog.close()
    window.hide()
    window.deleteLater()


def test_preview_summarizes_all_calls_by_model_without_paging(app, worker, monkeypatch):
    from PySide6.QtWidgets import QFrame, QPushButton
    from codexio.dashboard import request_cost_text
    worker._store.clear_index()
    now = datetime.now(timezone.utc) - timedelta(hours=2)
    rows = [dict(id="inline:%03d" % i, session_id="inline-session", turn_id="inline-turn",
                  timestamp=(now + timedelta(seconds=i)).isoformat(), model="gpt-6-astra", service_tier="default",
                  input_tokens=1000, output_tokens=100, total_tokens=1100, quality="response") for i in range(250)]
    worker._store.upsert_records(rows, "local")
    window = Dashboard(AppSettings(), {}, {})
    window.open_page("logs", "all")
    window.apply_data(publish(worker))
    monkeypatch.setattr(window._queries, "request_members", lambda *args, **kwargs: pytest.fail("Inspector should not read paged call details"))
    window._inspect_log_row(0)
    content = window._inspector_scroll.widget()
    summaries = [frame for frame in content.findChildren(QFrame) if frame.property("callSummary")]
    assert len(summaries) == 1
    labels = [label.text() for label in summaries[0].findChildren(QLabel)]
    assert "gpt-6-astra × 250" in labels
    assert request_cost_text(window._inspected_record, 6) in labels
    assert window._records == [] and window._dialogs == []
    assert not any(button.text() in ("上一页", "下一页") or button.property("memberCall") for button in content.findChildren(QPushButton))
    app.processEvents()
    window._inspector_scroll.verticalScrollBar().setValue(100)
    previous = window._inspector_scroll.verticalScrollBar().value()
    window._show_inspector(window._inspected_record, focus=False)
    app.processEvents()
    assert window._inspector_scroll.verticalScrollBar().value() == previous
    window.close()
