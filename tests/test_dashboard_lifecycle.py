from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel
from shiboken6 import isValid

from aiquota.dashboard import Dashboard
from aiquota.dashboard_host import DashboardHost
from aiquota.settings import AppSettings
from aiquota.update_manager import UpdateManager


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


def payload(cost=1):
    return {"records": [dict(id="r1", session_id="session1", turn_id="turn1",
        timestamp=datetime.now(timezone.utc).isoformat(), model="gpt-6-astra", service_tier="default",
        source_id="local", source_name="Local", input_tokens=100, output_tokens=10, total_tokens=110,
        cost_usd=cost, pricing_status="priced")], "summaries": {"today": {"tokens": 110, "usd": cost}},
        "available_models": ["gpt-6-astra"]}


def delete_pending(app):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_host_releases_closed_window_and_restores_latest_background_state(app):
    settings = AppSettings()
    config = {"theme": "dark", "widget_visible": True}
    geometries = []
    def save_geometry(value):
        geometries.append(value)
        config["main_geometry"] = value
    host = DashboardHost(lambda: settings, lambda: config, {"main_hidden": save_geometry}, app)
    host.apply_data(payload())
    host.set_usage_loading({"loading": False, "stage": "Ready"})
    host.apply_quota({"status": "ok", "plan_type": "pro"})
    assert host.dashboard is None
    first = host.open("logs")
    assert set(first._pages) == {"logs"}
    assert first._log_table.rowCount() == 1
    first.close()
    assert host.dashboard is None and geometries
    host.apply_data(payload(3))
    host.set_update_status("Update ready", False)
    host.apply_quota({"status": "ok", "plan_type": "plus"})
    # Reopen before the old deferred deletion: its destroyed signal must not
    # clear the new window or leave the controller pointing to a deleted widget.
    second = host.open("overview")
    delete_pending(app)
    assert not isValid(first)
    assert host.dashboard is second and isValid(second)
    assert second._latest_record["cost_usd"] == 3
    assert second._quota_plan.text() == "PLUS"
    assert not second._startup_banner.isVisible()
    second.open_page("settings")
    assert second._update_status.text() == "Update ready"
    second.close()
    delete_pending(app)
    assert host.dashboard is None and not isValid(second)
    host.deleteLater()


def test_page_queries_run_only_for_current_visible_page(app, monkeypatch):
    from aiquota import usage_queries
    calls = []
    class Queries:
        def __init__(self, _path): pass
        def page(self, **kwargs):
            calls.append(("page", kwargs.get("mode")))
            return dict(rows=[], total=0, pages=1, page=0, counts=dict(requests=0,subagents=0,unassigned=0))
        def chart_buckets(self, period, granularity, **kwargs):
            calls.append(("chart", period, granularity))
            return []
        def filters(self): return dict(models=[], sources=[])
    monkeypatch.setattr(usage_queries, "UsageQueries", Queries)
    window = Dashboard(AppSettings(), {}, {})
    data = dict(query_path="unused", query_generation=1, summaries={}, latest_request=None,
                filters={"models": [], "sources": []}, prices=[], available_models=[])
    window.apply_data(data)
    assert not window._pages and not calls
    window.open_page("logs")
    assert set(window._pages) == {"logs"}
    assert calls == [("page", "user_request")]
    window.apply_data(dict(data, query_generation=2))
    assert calls == [("page", "user_request")] * 2
    window.open_page("pricing")
    before = len(calls)
    window.apply_data(dict(data, query_generation=3))
    assert len(calls) == before
    window.open_page("overview")
    assert calls[-1] == ("chart", "week", "day")
    window.hide()
    before = len(calls)
    window.apply_data(dict(data, query_generation=4))
    window.apply_quota({"status": "ok", "plan_type": "pro"})
    assert len(calls) == before
    window.show()
    app.processEvents()
    assert len(calls) == before + 1
    window.showMinimized()
    app.processEvents()
    before = len(calls)
    window.apply_data(dict(data, query_generation=5))
    assert len(calls) == before
    window.showNormal()
    app.processEvents()
    assert len(calls) == before + 1
    window.close()
    delete_pending(app)


def test_update_manager_survives_dashboard_close(app):
    host = DashboardHost(AppSettings, lambda: {}, {}, app)
    updater = UpdateManager(app, lambda: None, available=False)
    updater.status_changed.connect(host.set_update_status)
    window = host.open("settings")
    updater.start()
    window.close()
    delete_pending(app)
    assert host.dashboard is None and isValid(updater)
    updater.status_changed.emit("Background update continues", False)
    reopened = host.open("settings")
    assert reopened._update_status.text() == "Background update continues"
    reopened.close()
    delete_pending(app)
    updater.stop()
    updater.deleteLater()
    host.deleteLater()


def test_closed_window_preserves_filters_and_unsaved_draft_without_widgets(app):
    import gc
    import json
    import weakref
    host = DashboardHost(AppSettings, lambda: {}, {}, app)
    data = payload()
    host.apply_data(data)
    first = host.open("logs")
    first._log_period.setCurrentIndex(first._log_period.findData("all"))
    first._log_mode.setCurrentIndex(first._log_mode.findData("model_call"))
    first._log_model.setCurrentIndex(first._log_model.findData("gpt-6-astra"))
    first._log_tier.setCurrentIndex(first._log_tier.findData("default"))
    first.open_page("settings")
    first._setting_widgets["background_opacity"].setValue(42)
    reference = weakref.ref(first)
    first.close()
    delete_pending(app)
    assert not isValid(first)
    del first
    gc.collect()
    assert reference() is None
    assert len(json.dumps(host._view_state)) < 2048
    second = host.open("logs")
    assert second._log_period.currentData() == "all"
    assert second._log_mode.currentData() == "model_call"
    assert second._log_model.currentData() == "gpt-6-astra"
    assert second._log_tier.currentData() == "default"
    assert set(second._pages) == {"logs"}
    second.open_page("settings")
    assert second._setting_widgets["background_opacity"].value() == 42
    second.close()
    delete_pending(app)
    # An explicit period passed by a tray/overview shortcut wins over saved state.
    third = host.open("logs", "today")
    assert third._log_period.currentData() == "today"
    third.close()
    delete_pending(app)
    host.deleteLater()


def test_hidden_session_info_does_not_leave_a_pending_tooltip(app):
    from aiquota.dashboard import SessionInfoButton
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data(payload())
    window.open_page("logs")
    button = window._log_table.cellWidget(0, 7).findChild(SessionInfoButton)
    button._timer.start(140)
    window.hide()
    assert not button._timer.isActive()
    button._show_tip()
    assert button._tip is None
    window.close()
    delete_pending(app)


def test_runtime_history_assignment_uses_compact_database_bounds(app, tmp_path):
    from datetime import timedelta
    from aiquota.pricing import PricingCatalog
    from aiquota.usage_store import UsageStore
    from aiquota.usage_queries import UsageQueries
    store = UsageStore(tmp_path / "usage.sqlite")
    row = payload()["records"][0]
    old = datetime.now(timezone.utc) - timedelta(days=200)
    row.update(timestamp=old.isoformat(), source_id="ssh:retired", source_name="Retired")
    store.upsert_records([row], "ssh:retired")
    queries = UsageQueries(store.path)
    generation = queries.rebuild(PricingCatalog(tmp_path / "prices"))
    window = Dashboard(AppSettings(), {}, {})
    window.apply_data(dict(query_path=str(store.path), query_generation=generation,
                           filters=queries.filters(), sources=[], summaries={}, prices=[]))
    window.open_page("settings")
    assert window._records == []
    window._assign_history()
    dialog = window._dialogs[-1]
    assert dialog.source.findData("ssh:retired") >= 0
    assert dialog.start.date().toPython() == old.astimezone().date()
    dialog.close()
    window.close()
    delete_pending(app)
