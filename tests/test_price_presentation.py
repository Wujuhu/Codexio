"""Dollar display changes must not quantize the stored usage or unit prices."""
import copy
import os
import re
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QLineEdit

from codexio.activity import UsageActivity
from codexio.charts import UsageChart, _axis_label
from codexio.dashboard import Dashboard, PriceEditor, price_rate_text, request_cost_text
from codexio.money import usd
from codexio.pricing import PricingCatalog
from codexio.settings import AppSettings
from codexio.usage_queries import compare_usage
from codexio.usage_queries import UsageQueries
from codexio.usage_store import UsageStore
from codexio.window import QuotaWindow


@pytest.fixture
def app():
    result = QApplication.instance() or QApplication([])
    result.setQuitOnLastWindowClosed(False)
    return result


@pytest.mark.parametrize("value,expected", [(0, "$0.00"), (1, "$1.00"), (1.235, "$1.24"),
    (1.234567, "$1.23"), (.000004, "$0.00"), (.005, "$0.01"), (1234.567, "$1,234.57"),
    (None, "未定价"), (True, "未定价"), (float("nan"), "未定价"), (float("inf"), "未定价")])
def test_dollar_amounts_have_exactly_two_decimals_without_treating_unknown_as_zero(value, expected):
    assert usd(value) == expected


def test_compact_axes_keep_two_fraction_digits_and_a_scale_suffix():
    for value, expected in ((1000, "$1.00K"), (1234567, "$1.23M"), (10**9, "$1.00B"), (10**18, "$1.00E")):
        assert usd(value, compact=True) == _axis_label(value, True) == expected
    assert price_rate_text(1234.567) == "1234.57"


@pytest.mark.parametrize("model,expected", [
    ("gpt-6-astra", (10, 1, 12.5, 50)), ("gpt-5.6-sol", (4, .4, 5, 20)),
    ("gpt-5.6-terra", (2, .2, 2.5, 12)), ("gpt-5.6-luna", (.2, .02, .25, 1.2)),
])
def test_regular_standard_uses_official_api_rates_for_all_token_categories(tmp_path, model, expected):
    # OpenAI's Standard short-context table, checked 2026-09-10:
    # https://developers.openai.com/api/docs/pricing
    catalog = PricingCatalog(tmp_path / "prices")
    standard = next(row for row in catalog.rows() if row["model"] == model and row["service_tier"] == "default" and not row["threshold"])
    assert tuple(standard[key] for key in ("input", "cache_read", "cache_write", "output")) == pytest.approx(expected)
    result = catalog.price(dict(model=model, provider="openai", service_tier="default", input_tokens=10000,
                                cached_input_tokens=3000, cache_write_input_tokens=2000, output_tokens=1000, total_tokens=11000))
    assert result["usd"] == pytest.approx((5000 * expected[0] + 3000 * expected[1] + 2000 * expected[2] + 1000 * expected[3]) / 1e6)
    assert "OpenAI API Standard" in result["reason"]


def test_standard_policy_change_reprices_historical_calls_groups_and_summaries(tmp_path):
    now = datetime.now().astimezone()
    store = UsageStore(tmp_path / "usage.sqlite")
    row = dict(id="historical", session_id="session", turn_id="turn", timestamp=now.isoformat(),
               model="gpt-6-astra", provider="openai", service_tier="default", quality="response",
               input_tokens=100000, cached_input_tokens=80000, cache_write_input_tokens=10000,
               output_tokens=2000, total_tokens=102000)
    store.upsert_records([row], "local")
    queries = UsageQueries(store.path)
    class PreviousPolicy:
        price_version = "previous-standard-write-as-input"
        def price(self, _record):
            return dict(usd=.38, pricing_status="priced")
    previous = queries.rebuild(PreviousPolicy())
    assert queries.record("historical")["cost_usd"] == .38
    catalog = PricingCatalog(tmp_path / "prices")
    assert queries.rebuild(catalog) > previous
    assert queries.record("historical")["cost_usd"] == pytest.approx(.405)
    assert queries.page(mode="user_request")["rows"][0]["cost_usd"] == pytest.approx(.405)
    assert queries.summaries(now)["today"]["usd"] == pytest.approx(.405)
    assert queries.period_comparison("today", now)["current"]["usd"] == pytest.approx(.405)


def test_rate_editor_keeps_unedited_underlying_precision(app):
    original = dict(model="gpt-test", input=1.235, cache_read=.000004, cache_write=None, output=9.876543)
    editor = PriceEditor(original)
    try:
        editor.show()
        app.processEvents()
        assert [editor.inputs[key].text() for key in ("input", "cache_read", "cache_write", "output")] == ["1.24", "0.00", "未定价", "9.88"]
        editor.model.setText("renamed-model")
        editor._save()
        for key in ("input", "cache_read", "cache_write", "output"):
            assert editor.rates()[key] == original[key]
        editor.inputs["output"].setValue(12.34)
        assert editor.rates()["output"] == 12.34
        assert editor.rates()["input"] == 1.235 and editor.rates()["cache_read"] == .000004
    finally:
        editor.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_standard_row_is_bold_in_all_six_columns_and_api_base_rows_are_absent(app, tmp_path, theme):
    catalog = PricingCatalog(tmp_path / "prices")
    window = Dashboard(AppSettings(), {"theme": theme}, {})
    try:
        window.apply_data(dict(available_models=["gpt-6-astra", "gpt-5.6-sol"], prices=catalog.rows(), standard_prices=catalog.standard_rows()))
        window.open_page("pricing")
        for index, price in enumerate(window._visible_prices):
            items = [window._price_table.item(index, column) for column in range(6)]
            standard = price["service_tier"] == "default" and not price.get("threshold")
            assert all(item.font().bold() == standard for item in items)
            assert "API 基准" not in items[1].text()
            assert all(re.fullmatch(r"\d+\.\d{2}|未定价", item.text()) for item in items[2:])
        window._price_search.setText("gpt-5.6-sol")
        assert window._price_table.rowCount() == 4
        assert all(window._price_table.item(0, column).font().bold() for column in range(6))
    finally:
        window.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_main_brand_uses_times_new_roman_in_both_themes(app, theme):
    window = Dashboard(AppSettings(), {"theme": theme}, {})
    try:
        window.open_page("logs")
        app.processEvents()
        name = window.findChild(QLabel, "brandName")
        icon = window.findChild(QLabel, "brandIcon")
        assert QFontInfo(name.font()).family() == "Times New Roman"
        assert icon.width() == icon.height() == 32
        assert name.contentsRect().left() == 4 and name.contentsRect().top() == 4
    finally:
        window.close()


def usage(stamp, ident, amount):
    return dict(timestamp=stamp.isoformat(), id=ident, session_id=ident, turn_id="turn", model="gpt-6-astra",
                total_tokens=100, input_tokens=90, output_tokens=10, cost_usd=amount, pricing_status="priced")


def test_logs_overview_trends_and_tooltips_share_two_decimal_display_only(app):
    now = datetime.now().astimezone()
    rows = [usage(now, "now", 1.234567), usage(now - timedelta(days=1), "before", .111111)]
    original = copy.deepcopy(rows)
    window = Dashboard(AppSettings(), {}, {})
    try:
        window.apply_data(dict(records=rows))
        window.open_page("logs")
        assert window._log_table.item(0, 4).text() == "$1.23"
        labels = [label.text() for label in window._inspector_scroll.findChildren(QLabel)]
        assert "$1.23" in labels
        assert not any(re.search(r"\$\d+\.\d{3}", text) for text in labels)
        window.open_page("overview")
        assert window._overview_cost.text() == "$1.23"
        assert "$1.23" in window._overview_comparisons["usd"].toolTip()
        window.open_page("trends")
        assert window._trend_metric_values["usd"].text() == "$1.23"
        comparison = compare_usage(rows, "today", now)
        assert comparison["current"]["usd"] == 1.234567
        assert comparison["changes"]["usd"]["percent"] == pytest.approx((1.234567 - .111111) / .111111 * 100)
        assert request_cost_text(dict(rows[0], pricing_status="partial")) == "$1.23\n部分未定价"
        bucket = dict(timestamp=now, tokens=100, usd=1.234567, requests=1, unpriced=0, input=90, output=10, cache_read=0, cache_write=0)
        chart = UsageChart()
        assert "价格  $1.23" in chart._tooltip_text(bucket)
        activity = UsageActivity()
        activity.set_buckets([bucket], now)
        assert "价格  $1.23" in activity.tooltip_text(now.date())
        chart.close()
        activity.close()
        assert rows == original
    finally:
        window.close()


def test_floating_summary_and_orb_use_two_decimal_currency(app):
    window = QuotaWindow(AppSettings(), lambda: None, lambda _: None, lambda: None)
    try:
        window.apply_usage_summary(dict(tokens=100, usd=1234.567))
        window._render_usage_summary()
        assert "$1,234.57" in window._usage_text
        assert "$1.23K" in window._orb_usage.text()
    finally:
        window.close()


def test_subscription_editor_formats_amount_without_rewriting_untouched_precision(app):
    window = Dashboard(AppSettings(), dict(subscription_profile=dict(plan="Pro", price_usd=123.456, renewal_date="")), {})
    try:
        window.open_page("subscription")
        assert window._subscription_price.text() == "$123.46"
        window._edit_subscription_profile()
        dialog = window._dialogs[-1]
        fields = dialog.findChildren(QLineEdit)
        assert any(field.text() == "123.46" for field in fields)
        buttons = dialog.findChild(QDialogButtonBox)
        buttons.button(QDialogButtonBox.StandardButton.Save).click()
        assert window._config["subscription_profile"]["price_usd"] == 123.456
    finally:
        window.close()
