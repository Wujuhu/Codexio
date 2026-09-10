from datetime import datetime, timedelta, timezone
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QTableWidget

from codexio.dashboard import Dashboard, PriceEditor, price_rate_text
from codexio.pricing import PricingCatalog
from codexio.settings import AppSettings
from codexio.usage_queries import UsageQueries
from codexio.usage_store import UsageStore


def test_price_revision_recomputes_calls_groups_charts_summaries_and_weekly_cache(tmp_path):
    store = UsageStore(tmp_path/"usage.sqlite")
    queries = UsageQueries(store.path)
    catalog = PricingCatalog(tmp_path/"prices")
    start = datetime(2026,9,10,tzinfo=timezone.utc)
    now = start+timedelta(hours=4)
    rows = [dict(id=ident,session_id="session",turn_id="turn",timestamp=(start+timedelta(hours=index+1)).isoformat(),
                 model=model,provider="openai",service_tier="fast",input_tokens=300000,cached_input_tokens=80000,
                 cache_write_input_tokens=10000,output_tokens=2000,reasoning_output_tokens=1500,total_tokens=302000,
                 quality="response",account_key="current",limit_id="codex",prompt_preview="计价一致性")
            for index,(ident,model) in enumerate((("a","gpt-6-astra"),("b","gpt-5.6-sol")))]
    store.upsert_records(rows,"local")
    store.upsert_turns([dict(id="turn",session_id="session",turn_id="turn",verified=True,
                            started_at=rows[0]["timestamp"],ended_at=(start+timedelta(hours=3)).isoformat(),status="completed")])
    store.upsert_observations([dict(id="q-"+str(used),timestamp=stamp.isoformat(),resets_at=(start+timedelta(days=7)).timestamp(),
                                   used_percent=used,window_minutes=10080,plan_type="pro",limit_id="codex",account_key="current")
                              for stamp,used in ((start,10),(start+timedelta(hours=3),12))],"local")
    class LegacyCatalog:
        price_version = "old-api-tier-prices"
        def price(self, record):
            return dict(usd=99,pricing_status="priced",price_version=self.price_version)
    previous = queries.rebuild(LegacyCatalog())
    since = (start-timedelta(days=1)).isoformat()
    old = queries.weekly_estimates(["local"],since,now=now)[0]
    assert old["estimated_total_usd"] == pytest.approx(9900)
    current = queries.rebuild(catalog)
    assert current > previous
    assert queries.record("a")["cost_usd"] == pytest.approx(5.95)
    assert queries.record("b")["cost_usd"] == pytest.approx(4.71)
    expected = 10.66
    calls = queries.page("model_call",start=start,end=now)["rows"]
    assert sum(row["cost_usd"] for row in calls) == pytest.approx(expected)
    request = queries.page("user_request",start=start,end=now)["rows"][0]
    assert request["call_count"]==2 and request["cost_usd"]==pytest.approx(expected)
    assert sum(row["cost_usd"] for row in queries.request_composition(request["id"]))==pytest.approx(expected)
    assert queries.summaries(now)["today"]["usd"] == pytest.approx(expected)
    assert sum(row["usd"] for row in queries.chart_buckets("all","hour",start=start,end=now)) == pytest.approx(expected)
    assert queries.weekly_estimates(["local"],since,now=now)[0]["estimated_total_usd"]==pytest.approx(expected*50)
    assert queries.rebuild(catalog)==current


def test_one_price_table_keeps_only_standard_fast_and_context_variants(tmp_path):
    app = QApplication.instance() or QApplication([])
    catalog = PricingCatalog(tmp_path/"prices")
    window = Dashboard(AppSettings(),{}, {})
    window.apply_data(dict(available_models=["gpt-6-astra","gpt-5.6-sol"],standard_prices=catalog.standard_rows(),prices=catalog.rows()))
    window.open_page("pricing")
    app.processEvents()
    assert window._pages["pricing"].findChildren(QTableWidget)==[window._price_table]
    assert window._price_table.rowCount()==6
    assert window._price_table.item(0,1).text()=="Standard"
    assert window._price_table.item(0,2).text()=="10.00"
    assert window._price_table.item(0,4).text()=="12.50"
    astra_fast = next(i for i,p in enumerate(window._visible_prices) if p["model"]=="gpt-6-astra" and p.get("service_tier")=="priority")
    assert window._price_table.item(astra_fast,2).text()=="25.00"
    assert window._price_table.item(astra_fast,4).text()=="25.00"
    assert window._price_table.item(astra_fast,5).text()=="125.00"
    window._price_table.selectRow(5)
    window._edit_selected_price()
    editor = window._dialogs[-1]
    assert editor.model.text()=="gpt-5.6-sol" and editor.inputs["input"].value()==4
    assert editor.rates()["service_tier"]=="default" and editor.rates()["threshold"]==0
    editor.close()
    window._price_search.setText("astra")
    for _ in range(4):
        app.processEvents()
    assert window._price_table.rowCount()==2
    assert window._selected_price_model is None and not window._edit_base_price.isEnabled()
    window._price_search.setText("unavailable-model")
    assert window._price_empty.isVisible()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_base_editor_and_display_distinguish_missing_free_and_small_prices():
    app = QApplication.instance() or QApplication([])
    editor = PriceEditor(dict(model="gpt-test",input=1,output=2,cache_read=None,cache_write=0))
    assert editor.rates()["cache_read"] is None and editor.rates()["cache_write"]==0
    assert price_rate_text(None)=="未定价"
    assert price_rate_text(0)=="0.00" and price_rate_text(.000004)=="0.00"
    assert price_rate_text(.39999999999999997)=="0.40" and price_rate_text(.175)=="0.18"
    editor.close()
