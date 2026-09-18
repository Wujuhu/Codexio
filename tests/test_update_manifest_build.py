from __future__ import annotations

import json
import plistlib
import zipfile
from pathlib import Path
import subprocess
import sys

import pytest

from codexio.updates import file_sha256, release_from_manifest

ROOT = Path(__file__).resolve().parents[1]


def build_manifest(system, asset, version, output, base):
    return subprocess.run([sys.executable, str(ROOT / "scripts/update_manifest.py"), "--platform", system,
                           "--asset", str(asset), "--version", version, "--output", str(output),
                           "--base", str(base), "--architecture", "arm64"],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)


def test_both_build_orders_preserve_other_platform_and_old_windows_contract(tmp_path):
    exe = tmp_path / "Codexio.exe"
    exe.write_bytes(b"MZwindows fixture")
    app_zip = tmp_path / "Codexio.app.zip"
    app_zip.write_bytes(b"APP ZIP fixture")
    base = tmp_path / "base.json"
    base.write_text("{}", encoding="utf-8")
    windows_manifest = tmp_path / "windows/latest.json"
    assert build_manifest("windows", exe, "0.2.4", windows_manifest, base).returncode == 0
    windows = json.loads(windows_manifest.read_text(encoding="utf-8"))
    merged_manifest = tmp_path / "macos/latest.json"
    assert build_manifest("macos", app_zip, "0.2.5", merged_manifest, windows_manifest).returncode == 0
    merged = json.loads(merged_manifest.read_text(encoding="utf-8"))
    assert {k: v for k, v in merged.items() if k != "macos"} == windows
    assert release_from_manifest(merged, "0.2.3").url.endswith("/v0.2.4/Codexio.exe")
    assert release_from_manifest(merged, "0.2.4", asset_name="Codexio.app.zip", manifest_key="macos",
                                 architecture="arm64").sha256 == file_sha256(app_zip)
    # Building Windows last updates its fields without erasing the Mac entry.
    exe.write_bytes(b"MZupdated windows fixture")
    assert build_manifest("windows", exe, "0.2.5", windows_manifest, merged_manifest).returncode == 0
    windows = json.loads(windows_manifest.read_text(encoding="utf-8"))
    assert windows["macos"] == merged["macos"]
    assert windows["version"] == "0.2.5" and windows["sha256"] == file_sha256(exe)
    # Rebuilding Mac can safely read and write the same shared manifest.
    app_zip.write_bytes(b"rebuilt APP ZIP fixture")
    assert build_manifest("macos", app_zip, "0.2.5", windows_manifest, windows_manifest).returncode == 0
    rebuilt = json.loads(windows_manifest.read_text(encoding="utf-8"))
    assert rebuilt["sha256"] == windows["sha256"]
    assert rebuilt["macos"]["sha256"] == file_sha256(app_zip)


def test_invalid_base_preserves_previous_manifest(tmp_path):
    base = tmp_path / "base.json"
    base.write_text('{"version":"0.2.4","url":"https://example.com/bad.exe"}', encoding="utf-8")
    app_zip = tmp_path / "Codexio.app.zip"
    app_zip.write_bytes(b"APP ZIP fixture")
    output = tmp_path / "latest.json"
    output.write_text("previous manifest", encoding="utf-8")
    assert build_manifest("macos", app_zip, "0.2.4", output, base).returncode != 0
    assert output.read_text(encoding="utf-8") == "previous manifest"


def test_default_manifest_comes_from_same_release_version(tmp_path, monkeypatch):
    from scripts import update_manifest

    monkeypatch.setattr(update_manifest, "ROOT", tmp_path)
    old = tmp_path / "release/0.2.3/latest.json"
    current = tmp_path / "release/0.2.4/latest.json"
    legacy = tmp_path / "build/dev/macos/latest.json"
    for path, version in ((old, "0.2.3"), (current, "0.2.4"), (legacy, "0.2.2")):
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"version": version, "macos": {"version": version}}), encoding="utf-8")
    for system in ("windows", "macos"):
        assert update_manifest.load_base(system, version="0.2.4")["version"] == "0.2.4"
    assert json.loads(old.read_text(encoding="utf-8"))["version"] == "0.2.3"


@pytest.fixture
def complete_release(tmp_path):
    directory = tmp_path / "release/0.2.4"
    directory.mkdir(parents=True)
    exe = directory / "Codexio.exe"
    exe.write_bytes(b"MZwindows fixture")
    app_zip = directory / "Codexio.app.zip"
    with zipfile.ZipFile(app_zip, "w") as archive:
        archive.writestr("Codexio.app/Contents/MacOS/Codexio", b"Mac binary")
        archive.writestr("Codexio.app/Contents/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": "com.wujuhu.codexio", "CFBundleExecutable": "Codexio",
            "CFBundleShortVersionString": "0.2.4", "CFBundleVersion": "0.2.4"}))
    base = tmp_path / "base.json"
    base.write_text("{}", encoding="utf-8")
    manifest = directory / "latest.json"
    assert build_manifest("windows", exe, "0.2.4", manifest, base).returncode == 0
    assert build_manifest("macos", app_zip, "0.2.4", manifest, manifest).returncode == 0
    return directory


def test_complete_release_accepts_matching_packages(complete_release):
    from scripts.verify_release import verify_release

    verify_release(complete_release, "0.2.4")
    assert {p.name for p in complete_release.iterdir()} == {"Codexio.exe", "Codexio.app.zip", "latest.json"}


@pytest.mark.parametrize("failure", ["missing_zip", "corrupt_exe", "wrong_macos_version"])
def test_incomplete_or_mismatched_release_cannot_publish(complete_release, failure):
    from codexio.updates import UpdateError
    from scripts.verify_release import verify_release

    if failure == "missing_zip":
        (complete_release / "Codexio.app.zip").unlink()
    elif failure == "corrupt_exe":
        exe = complete_release / "Codexio.exe"
        exe.write_bytes(b"MZ" + b"x" * (exe.stat().st_size - 2))
    else:
        path = complete_release / "latest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["macos"]["version"] = "0.2.3"
        data["macos"]["url"] = data["macos"]["url"].replace("v0.2.4", "v0.2.3")
        path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UpdateError):
        verify_release(complete_release, "0.2.4")


def test_release_archiving_is_explicit_and_preserves_previous_versions(complete_release, tmp_path):
    from scripts.prepare_release import prepare_release
    from codexio.updates import UpdateError

    root = tmp_path / "project"
    historical = root / "release/0.2.3"
    historical.mkdir(parents=True)
    (historical / "keep").write_bytes(b"old release")
    destination = prepare_release("0.2.4", complete_release, complete_release, root=root)
    assert destination == root / "release/0.2.4"
    assert {p.name for p in destination.iterdir()} == {"Codexio.exe", "Codexio.app.zip", "latest.json"}
    assert (historical / "keep").read_bytes() == b"old release"
    with pytest.raises(UpdateError, match="已存在"):
        prepare_release("0.2.4", complete_release, complete_release, root=root)
    with pytest.raises(UpdateError):
        prepare_release("0.2.5", complete_release, complete_release, root=root)
    assert not (root / "release/0.2.5").exists()
    assert list((root / "build/staging/release").iterdir()) == []


def test_first_mac_development_build_does_not_require_windows_or_network(tmp_path, monkeypatch):
    from scripts import update_manifest

    monkeypatch.setattr(update_manifest, "ROOT", tmp_path)
    asset = tmp_path / "Codexio.app.zip"
    asset.write_bytes(b"APP ZIP fixture")
    output = tmp_path / "build/dev/macos/latest.json"
    data = update_manifest.write_manifest("macos", asset, "0.2.4", output)
    assert data["macos"]["url"].endswith("/v0.2.4/Codexio.app.zip")
    assert not (tmp_path / "release").exists()
