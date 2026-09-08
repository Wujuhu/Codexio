from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from aiquota.pricing import PricingCatalog, LITELLM_URL, MODELS_DEV_URL, _litellm_rows, _models_dev_rows


def usage(**changes):
    result = dict(model="gpt-6-astra", provider="openai", service_tier="default", input_tokens=100000,
                  cached_input_tokens=80000, cache_write_input_tokens=10000, output_tokens=2000,
                  reasoning_output_tokens=1500, total_tokens=102000)
    result.update(changes)
    return result


def test_token_subsets_and_reasoning_not_double_charged(tmp_path):
    result = PricingCatalog(tmp_path).price(usage())
    assert result["pricing_status"] == "priced"
    assert result["usd"] == pytest.approx(.1 + .08 + .125 + .1)


def test_long_context_and_priority_combination_is_applied_once(tmp_path):
    catalog = PricingCatalog(tmp_path)
    record = usage(service_tier="priority", input_tokens=300000, total_tokens=302000)
    result = catalog.price(record)
    assert result["rates"]["threshold"] == 272000
    assert result["usd"] == pytest.approx(210000 * 40 / 1e6 + 80000 * 4 / 1e6 + 10000 * 50 / 1e6 + 2000 * 150 / 1e6)
    assert catalog.price(usage(input_tokens=272000, total_tokens=274000))["rates"]["threshold"] == 0
    fast_result = catalog.price(dict(record, service_tier="fast"))
    assert fast_result["pricing_status"] == "priced"
    assert fast_result["usd"] == result["usd"]


@pytest.mark.parametrize("changes", [dict(cached_input_tokens=100001), dict(cache_write_input_tokens=-1),
                                     dict(input_tokens=float('nan')), dict(total_tokens=5),
                                     dict(reasoning_output_tokens=2001), dict(output_tokens=1.5)])
def test_invalid_usage_never_returns_a_dollar_value(tmp_path, changes):
    assert PricingCatalog(tmp_path).price(usage(**changes))["pricing_status"] == "invalid"


def test_unknown_provider_and_model_are_not_guessed(tmp_path):
    catalog = PricingCatalog(tmp_path)
    assert catalog.price(usage(provider="unknown"))["usd"] is None
    assert catalog.price(usage(model="codex-auto-review"))["usd"] is None
    assert catalog.price(usage(model="gpt-reserve"))["usd"] is None
    result = catalog.price(usage(service_tier=None))
    assert result["pricing_status"] == "estimated"
    assert result["usd"] is not None


def test_manual_prices_persist_and_remain_locked_after_sync(tmp_path, monkeypatch):
    catalog = PricingCatalog(tmp_path)
    original = catalog.price(usage())["usd"]
    version = catalog.price_version
    catalog.set_override("gpt-6-astra", dict(input=1, cache_read=.1, cache_write=2, output=4))
    assert catalog.price_version != version
    changed = catalog.price(usage())["usd"]
    assert changed != original
    catalog = PricingCatalog(tmp_path)
    monkeypatch.setattr(catalog, "_fetch", lambda *args: ({"gpt-6-astra": {"litellm_provider": "openai", "mode": "chat",
                        "input_cost_per_token": .001, "output_cost_per_token": .002}}, '"v2"', False))
    assert catalog.sync(True)["status"] == "synced"
    assert catalog.price(usage())["usd"] == changed
    assert catalog.price(usage())["rates"]["locked"] is True
    catalog.set_override("gpt-6-astra", None)
    assert catalog.price(usage())["usd"] != changed


def test_primary_parser_preserves_explicit_combined_rates():
    rows = _litellm_rows({"gpt-test": {"litellm_provider": "openai", "mode": "responses",
                         "input_cost_per_token": 1e-6, "output_cost_per_token": 4e-6,
                         "input_cost_per_token_above_272k_tokens_priority": 9e-6,
                         "output_cost_per_token_above_272k_tokens_priority": 19e-6}}, "date")
    assert rows[1]["threshold"] == 272000
    assert rows[1]["input"] == 9
    assert rows[1]["output"] == 19
    assert rows[1]["cache_read"] is None


def test_fallback_is_standard_only_and_retains_old_cache_when_offline(tmp_path, monkeypatch):
    catalog = PricingCatalog(tmp_path)
    def fetch(url, etag=None):
        if url == LITELLM_URL:
            raise OSError("offline")
        return {"openai": {"models": {"gpt-test": {"cost": {"input": 1, "output": 4, "cache_read": .1}}}}}, "etag", False
    monkeypatch.setattr(catalog, "_fetch", fetch)
    assert catalog.sync(True)["status"] == "fallback"
    assert catalog.price(usage(model="gpt-test", cache_write_input_tokens=0))["usd"] is not None
    assert catalog.price(usage(model="gpt-test", service_tier="priority", cache_write_input_tokens=0))["usd"] is None
    monkeypatch.setattr(catalog, "_fetch", lambda *args: (_ for _ in ()).throw(OSError("offline")))
    assert catalog.sync(True)["status"] == "offline"
    assert PricingCatalog(tmp_path).price(usage(model="gpt-test", cache_write_input_tokens=0))["usd"] is not None


def test_sync_throttle_and_etag_304(tmp_path, monkeypatch):
    catalog = PricingCatalog(tmp_path)
    calls = []
    payload = {"gpt-test": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}
    monkeypatch.setattr(catalog, "_fetch", lambda url, etag=None: (calls.append(etag) or payload, "etag", False))
    catalog.sync()
    catalog.sync()
    assert len(calls) == 2  # Primary feed plus independent standard-price check.
    version = catalog.price_version
    monkeypatch.setattr(catalog, "_fetch", lambda url, etag=None: (calls.append(etag), etag, True))
    result = catalog.sync(True)
    assert "etag" in calls[2:]
    assert result["changed"] is False
    assert catalog.price_version == version


def test_bad_cache_is_ignored_and_override_validation(tmp_path):
    (tmp_path / "pricing_cache.json").write_text("not json", encoding="utf-8")
    catalog = PricingCatalog(tmp_path)
    assert catalog.price(usage())["usd"] is not None
    with pytest.raises(ValueError):
        catalog.set_override("gpt-6-astra", dict(input=-1, cache_read=0, cache_write=0, output=1))


def test_content_addressed_versions_are_immutable(tmp_path):
    catalog = PricingCatalog(tmp_path)
    first_version = catalog.price_version
    original = (tmp_path / "versions" / (first_version + ".json")).read_bytes()
    catalog.load()
    assert (tmp_path / "versions" / (first_version + ".json")).read_bytes() == original
    catalog.set_override("gpt-6-astra", dict(input=1, cache_read=.1, cache_write=2, output=4))
    archive = tmp_path / "versions" / (catalog.price_version + ".json")
    assert archive.exists()
    assert json.loads(archive.read_text(encoding="utf-8"))["price_version"] != first_version


def test_conflicting_sources_preserve_existing_model_prices(tmp_path, monkeypatch):
    catalog = PricingCatalog(tmp_path)
    old_price = catalog.price(usage())["usd"]
    def fetch(url, etag=None):
        if url == LITELLM_URL:
            return {"gpt-6-astra": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 2e-6,
                                    "output_cost_per_token": 4e-6}}, "a", False
        return {"openai": {"models": {"gpt-6-astra": {"cost": {"input": 3, "output": 4}}}}}, "b", False
    monkeypatch.setattr(catalog, "_fetch", fetch)
    result = catalog.sync(True)
    assert result["status"] == "conflict"
    assert "gpt-6-astra" in result["conflicting_models"]
    assert catalog.price(usage())["usd"] == old_price


def test_offline_attempt_retries_after_one_hour(tmp_path, monkeypatch):
    catalog = PricingCatalog(tmp_path)
    calls = []
    monkeypatch.setattr(catalog, "_fetch", lambda *args: (calls.append(args) or (_ for _ in ()).throw(OSError("offline"))))
    catalog.sync(True)
    assert len(calls) == 2
    catalog._cache["checked_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    catalog.sync()
    assert len(calls) == 4


def test_primary_success_not_blocked_by_missing_reference(tmp_path, monkeypatch):
    catalog = PricingCatalog(tmp_path)
    def fetch(url, etag=None):
        if url == MODELS_DEV_URL:
            raise OSError("reference offline")
        return {"gpt-test": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 1e-6,
                              "output_cost_per_token": 2e-6}}, "a", False
    monkeypatch.setattr(catalog, "_fetch", fetch)
    result = catalog.sync(True)
    assert result["status"] == "synced"
    assert result["verification_status"] == "not_available"
    assert result["warning"]
    assert catalog.price(usage(model="gpt-test", cache_write_input_tokens=0, cached_input_tokens=0))["usd"] is not None


def test_malformed_object_cache_does_not_disable_bundled_prices(tmp_path):
    (tmp_path / "pricing_cache.json").write_text('{"rows": null}', encoding="utf-8")
    catalog = PricingCatalog(tmp_path)
    assert catalog.price(usage())["usd"] is not None


def test_aggregate_history_is_never_marked_exactly_priced(tmp_path):
    result = PricingCatalog(tmp_path).price(usage(quality="cumulative_observation", service_tier="priority"))
    assert result["pricing_status"] == "estimated"
    assert result["usd"] is not None
    assert result["rates"]["service_tier"] == "priority"


def test_conflict_does_not_poison_etag_when_feeds_later_agree(tmp_path, monkeypatch):
    catalog = PricingCatalog(tmp_path)
    reference_price = [3]
    primary_etags = []
    def fetch(url, etag=None):
        if url == LITELLM_URL:
            primary_etags.append(etag)
            return {"gpt-6-astra": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 2e-6,
                                    "output_cost_per_token": 4e-6}}, "a", False
        return {"openai": {"models": {"gpt-6-astra": {"cost": {"input": reference_price[0], "output": 4}}}}}, "b", False
    monkeypatch.setattr(catalog, "_fetch", fetch)
    assert catalog.sync(True)["status"] == "conflict"
    reference_price[0] = 2
    assert catalog.sync(True)["status"] == "synced"
    assert primary_etags == [None, None]
