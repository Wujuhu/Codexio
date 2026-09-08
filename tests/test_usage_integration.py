from datetime import datetime, timezone, timedelta
import json

from aiquota.analytics_config import load_analytics_config, save_analytics_config
from aiquota.rate_limits import parse_rate_limits_result, snapshot_to_cache, snapshot_from_cache, merge_rate_limit_snapshots
from aiquota.usage_worker import summarize


def test_raw_percentage_and_multi_bucket_survive_cache():
    snapshot = parse_rate_limits_result({"rateLimits": {"limitId": "spark"}, "rateLimitsByLimitId": {
        "codex": {"planType": "pro", "primary": {"usedPercent": 10.42, "windowDurationMins": 10080, "resetsAt": 1800000000}},
        "spark": {"primary": {"usedPercent": 85.1, "windowDurationMins": 10080, "resetsAt": 1800000100}},
    }})
    assert snapshot.limit_id == "codex"
    assert snapshot.primary.used_percent == 10.42
    loaded, _ = snapshot_from_cache(snapshot_to_cache(snapshot, datetime.now(timezone.utc)))
    assert loaded.primary.used_percent == 10.42
    assert loaded.by_limit["spark"].primary.used_percent == 85.1


def test_other_bucket_notification_does_not_replace_codex_primary():
    base = parse_rate_limits_result({"rateLimitsByLimitId": {
        "codex": {"primary": {"usedPercent": 20, "windowDurationMins": 10080}},
        "spark": {"primary": {"usedPercent": 30, "windowDurationMins": 10080}},
    }})
    update = parse_rate_limits_result({"rateLimits": {"limitId": "spark", "primary": {"usedPercent": 90}}})
    merged = merge_rate_limit_snapshots(base, update)
    assert merged.limit_id == "codex"
    assert merged.primary.used_percent == 20
    assert merged.by_limit["spark"].primary.used_percent == 90
    for _ in range(200):
        merged = merge_rate_limit_snapshots(merged, update)
    assert all(bucket.by_limit is None for bucket in merged.by_limit.values())


def test_analytics_preferences_preserve_account_epoch(tmp_path):
    path = tmp_path / "analytics.json"
    original = load_analytics_config(path)
    original.update(theme="dark", widget_visible=False, ssh_sources=[{"id": "test", "host": "sample", "enabled": False}])
    save_analytics_config(original, path)
    restored = load_analytics_config(path)
    assert restored == original
    assert restored["account_since"] == original["account_since"]


def test_summary_cached_subset_unknown_price_and_midnight():
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    midnight = now.replace(hour=0)
    rows = [
        {"timestamp": midnight.isoformat(), "input_tokens": 100, "cached_input_tokens": 80,
         "output_tokens": 10, "total_tokens": 110, "cost_usd": .01, "quality": "response"},
        {"timestamp": (midnight - timedelta(seconds=1)).isoformat(), "input_tokens": 300,
         "total_tokens": 350, "cost_usd": .03, "quality": "response"},
        {"timestamp": (midnight + timedelta(minutes=1)).isoformat(), "input_tokens": 200,
         "total_tokens": 220, "cost_usd": None, "quality": "response"},
    ]
    result = summarize(rows, now)
    assert result["today"]["tokens"] == 330
    assert result["today"]["usd"] == .01
    assert result["today"]["unpriced_tokens"] == 220
    assert result["today"]["cache_hit_rate"] == 80 / 300
    assert result["week"]["tokens"] == 680




def test_all_unpriced_chart_bucket_is_a_gap_not_zero_dollars():
    from aiquota.charts import bucket_records
    now = datetime.now().astimezone()
    result = bucket_records([{"timestamp": now.isoformat(), "input_tokens": 10,
                             "total_tokens": 10, "cost_usd": None}], now=now)
    observed = [b for b in result if b["requests"]]
    assert len(observed) == 1
    assert observed[0]["usd"] is None


def test_docked_widget_keeps_quota_only_and_original_minimum(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from PySide6.QtWidgets import QApplication, QLabel
    from aiquota.settings import AppSettings
    from aiquota.window import QuotaWindow
    app = QApplication.instance() or QApplication([])
    widget = QuotaWindow(AppSettings(dock_edge="top"), lambda: None, lambda _: None, lambda: None)
    original_minimum = widget._docked_min_size("top")
    widget.apply_usage_summary({"tokens": 1234567, "usd": 12.34, "unpriced_tokens": 0})
    widget.show()
    app.processEvents()
    assert widget._docked_min_size("top") == original_minimum
    assert not widget._status.isVisible()
    assert not widget._orb_usage.isVisible()
    assert all("Token" not in label.text() and "$" not in label.text()
               for label in widget._dock_strip.findChildren(QLabel))
    widget._apply_dock("none", persist=False)
    app.processEvents()
    assert widget._status.isVisible()
    assert widget._status.text() == "今日 1.23M Token $12.34"
    assert widget._status.font().pixelSize() >= 15
    widget.close()
