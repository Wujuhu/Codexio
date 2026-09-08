from __future__ import annotations

import hashlib
import io
import json
import threading
from urllib.error import HTTPError

import pytest

from aiquota import updates


PACKAGE = b"MZ" + b"test executable payload" * 100


def manifest(**changes):
    data = {"version": "0.1.2", "url": "https://github.com/Wujuhu/AIQuota/releases/download/v0.1.2/AIQuota.exe",
            "sha256": hashlib.sha256(PACKAGE).hexdigest(), "size": len(PACKAGE), "notes": "test release"}
    data.update(changes)
    return data


class Response(io.BytesIO):
    def geturl(self):
        return "https://release-assets.githubusercontent.com/test"


def test_update_version_order_and_no_downgrades():
    assert updates.version_tuple("v0.1.10") > updates.version_tuple("0.1.9")
    assert updates.release_from_manifest(manifest(), "0.1.2") is None
    assert updates.release_from_manifest(manifest(), "1.0.0") is None
    assert updates.release_from_manifest(manifest(), "0.1.1").version == "0.1.2"


@pytest.mark.parametrize("version", ["0.1", "v1.2.3-beta", "01.2.3", "1.2.65536", "https://example.com", None, "9" * 10000])
def test_rejects_invalid_versions(version):
    with pytest.raises(updates.UpdateError):
        updates.version_tuple(version)


@pytest.mark.parametrize("changes", [
    {"url": "http://github.com/Wujuhu/AIQuota/releases/download/v0.1.2/AIQuota.exe"},
    {"url": "https://github.com/Other/AIQuota/releases/download/v0.1.2/AIQuota.exe"},
    {"url": "https://github.com/Wujuhu/AIQuota/releases/download/v0.1.3/AIQuota.exe"},
    {"url": "https://github.com/Wujuhu/AIQuota/releases/download/v0.1.2/AIQuota.exe?redirect=evil"},
    {"sha256": "missing"}, {"size": True}, {"size": -1}, {"size": updates.MAX_DOWNLOAD_BYTES + 1},
])
def test_manifest_rejects_untrusted_or_incomplete_downloads(changes):
    with pytest.raises(updates.UpdateError):
        updates.release_from_manifest(manifest(**changes), "0.1.1")


def test_fetch_uses_static_release_attachment_not_rate_limited_api(monkeypatch):
    requested = []
    def open_url(request, timeout):
        requested.append(request.full_url)
        return Response(json.dumps(manifest()).encode("utf-8"))
    monkeypatch.setattr(updates, "urlopen", open_url)
    assert updates.fetch_release("0.1.1").version == "0.1.2"
    assert requested == [updates.LATEST_MANIFEST]


def test_new_repository_without_releases_is_normal(monkeypatch):
    def missing(request, timeout):
        raise HTTPError(request.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(updates, "urlopen", missing)
    assert updates.fetch_release("0.1.1") is None


@pytest.mark.parametrize("payload", [b"MZwrong data", PACKAGE[:-1], PACKAGE + b"extra"])
def test_corrupt_or_incomplete_download_never_becomes_installable(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: Response(payload))
    target = tmp_path / "package.bin"
    with pytest.raises(updates.UpdateError):
        updates.download_release(updates.release_from_manifest(manifest(), "0.1.1"), target, threading.Event())
    assert not target.exists()
    assert not target.with_suffix(".part").exists()


def test_verified_download_and_cancellation(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: Response(PACKAGE))
    release = updates.release_from_manifest(manifest(), "0.1.1")
    target = tmp_path / "package.bin"
    progress = []
    updates.download_release(release, target, threading.Event(), progress.append)
    assert target.read_bytes() == PACKAGE and progress[-1] == 100
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(updates.UpdateCancelled):
        updates.download_release(release, tmp_path / "cancelled.bin", cancel)
    assert not (tmp_path / "cancelled.bin").exists()


def test_cancel_after_read_preserves_the_current_app(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "urlopen", lambda *_args, **_kwargs: Response(PACKAGE))
    cancel = threading.Event()
    with pytest.raises(updates.UpdateCancelled):
        updates.download_release(updates.release_from_manifest(manifest(), "0.1.1"), tmp_path / "package.bin",
                                 cancel, lambda _value: cancel.set())
    assert not list(tmp_path.iterdir())
