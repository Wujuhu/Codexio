from __future__ import annotations

import json

import pytest

from codexio import available_models
from codexio.available_models import load_available_models


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(available_models, "_LAST_VALID", {})


def write_cache(root, models, **extra):
    root.mkdir(parents=True, exist_ok=True)
    path = root / "models_cache.json"
    payload = dict(fetched_at="2026-09-07T11:25:51Z", models=models, **extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_selector_visibility_is_independent_of_api_availability(tmp_path):
    write_cache(tmp_path, [
        dict(slug="gpt-6-astra", visibility="list", supported_in_api=True, priority=1),
        dict(slug="gpt-5.3-codex-spark", visibility="list", supported_in_api=False, priority=26),
        dict(slug="codex-auto-review", visibility="hide", supported_in_api=True),
        dict(slug="gpt-reserve", visibility="hide", supported_in_api=True),
    ])
    data = load_available_models([str(tmp_path)])
    assert data == dict(models=["gpt-6-astra", "gpt-5.3-codex-spark"], status="ok",
                        updated_at="2026-09-07T11:25:51+00:00")


def test_model_list_shape_supports_explicit_hidden_flag_and_unknown_prices(tmp_path):
    write_cache(tmp_path, [
        dict(id="id-for-new-model", model="new-model-without-price", hidden=False),
        dict(id="legacy-codex", hidden=False),
        dict(model="internal", hidden=True),
        dict(slug="conflicting", visibility="list", hidden=True),
    ])
    assert load_available_models([str(tmp_path)])["models"] == ["new-model-without-price", "legacy-codex"]


def test_malformed_identifiers_and_ambiguous_visibility_are_not_exposed(tmp_path):
    write_cache(tmp_path, [
        None, "gpt-anything", 1,
        dict(slug="<script>alert(1)</script>", visibility="list"),
        dict(slug="model\nname", visibility="list"),
        dict(slug="../auth.json", visibility="list"),
        dict(slug="https://example.com/model", visibility="list"),
        dict(slug="future-policy", visibility="unknown"),
        dict(slug="missing-policy"),
        dict(slug="string-flag", hidden="false"),
        dict(slug="valid", visibility="list"),
    ])
    assert load_available_models([str(tmp_path)])["models"] == ["valid"]


@pytest.mark.parametrize("payload", [None, [], {}, {"data": []}, {"models": {}},
                                      {"models": ["gpt-6-astra"]},
                                      {"models": [{"slug": "model-without-selector-flags"}]}])
def test_unknown_or_broken_schema_never_falls_back_to_api_catalog(tmp_path, payload):
    (tmp_path / "models_cache.json").write_text(json.dumps(payload), encoding="utf-8")
    result = load_available_models([str(tmp_path)])
    assert result["models"] == []
    assert result["status"] == "unavailable"


@pytest.mark.parametrize("failure", ["missing", "json", "encoding", "schema"])
def test_unreadable_cache_retains_last_valid_list(tmp_path, failure):
    path = write_cache(tmp_path, [dict(slug="available", visibility="list")])
    load_available_models([str(tmp_path)])
    if failure == "missing":
        path.unlink()
    elif failure == "json":
        path.write_text('{"models":', encoding="utf-8")
    elif failure == "encoding":
        path.write_bytes(b"\xff\xfe\xff")
    else:
        path.write_text('{"unexpected": []}', encoding="utf-8")
    result = load_available_models([str(tmp_path)])
    assert result["models"] == ["available"]
    assert result["status"] == "stale"
    assert result["updated_at"] == "2026-09-07T11:25:51+00:00"


def test_recovery_replaces_retained_list_and_explicit_empty_clears_it(tmp_path):
    path = write_cache(tmp_path, [dict(slug="before", visibility="list")])
    first = load_available_models([str(tmp_path)])
    first["models"].append("caller-cannot-mutate-snapshot")
    path.write_text("not json", encoding="utf-8")
    assert load_available_models([str(tmp_path)])["models"] == ["before"]
    write_cache(tmp_path, [dict(slug="after-refresh", visibility="list")])
    assert load_available_models([str(tmp_path)])["models"] == ["after-refresh"]
    write_cache(tmp_path, [])
    result = load_available_models([str(tmp_path)])
    assert result["models"] == []
    assert result["status"] == "ok"


def test_all_hidden_is_a_valid_empty_list(tmp_path):
    write_cache(tmp_path, [dict(slug="internal", visibility="hide")])
    result = load_available_models([str(tmp_path)])
    assert result["models"] == []
    assert result["status"] == "ok"


def test_multiple_roots_priority_deduplication_and_removed_root(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write_cache(a, [dict(slug="later", visibility="list", priority=20),
                    dict(slug="first", visibility="list", priority=1),
                    dict(slug="first", visibility="list", priority=5)])
    write_cache(b, [dict(slug="first", visibility="list"), dict(slug="other", visibility="list")])
    result = load_available_models([str(a), str(a), str(b)])
    assert result["models"] == ["first", "later", "other"]
    assert result["status"] == "ok"
    assert load_available_models([str(b)])["models"] == ["first", "other"]


def test_partial_roots_are_marked_stale_and_no_roots_has_no_list(tmp_path):
    write_cache(tmp_path, [dict(slug="available", visibility="list")])
    result = load_available_models([str(tmp_path), str(tmp_path / "missing")])
    assert result["models"] == ["available"]
    assert result["status"] == "stale"
    assert load_available_models([])["status"] == "unavailable"
    assert load_available_models([None, "", "\0"])["status"] == "unavailable"


def test_utf8_bom_and_invalid_timestamp_use_file_metadata(tmp_path):
    payload = dict(models=[dict(slug="available", visibility="list")], fetched_at="not-a-date")
    (tmp_path / "models_cache.json").write_text(json.dumps(payload), encoding="utf-8-sig")
    result = load_available_models([str(tmp_path)])
    assert result["status"] == "ok"
    assert result["updated_at"].endswith("+00:00")


def test_oversized_cache_is_rejected(tmp_path, monkeypatch):
    write_cache(tmp_path, [dict(slug="available", visibility="list")])
    monkeypatch.setattr(available_models, "_MAX_CACHE_BYTES", 16)
    assert load_available_models([str(tmp_path)])["status"] == "unavailable"
