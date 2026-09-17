from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

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
    dmg = tmp_path / "Codexio.dmg"
    dmg.write_bytes(b"DMG fixture")
    base = tmp_path / "base.json"
    base.write_text("{}", encoding="utf-8")
    windows_manifest = tmp_path / "windows/latest.json"
    assert build_manifest("windows", exe, "0.2.4", windows_manifest, base).returncode == 0
    windows = json.loads(windows_manifest.read_text(encoding="utf-8"))
    merged_manifest = tmp_path / "macos/latest.json"
    assert build_manifest("macos", dmg, "0.2.5", merged_manifest, windows_manifest).returncode == 0
    merged = json.loads(merged_manifest.read_text(encoding="utf-8"))
    assert {k: v for k, v in merged.items() if k != "macos"} == windows
    assert release_from_manifest(merged, "0.2.3").url.endswith("/v0.2.4/Codexio.exe")
    assert release_from_manifest(merged, "0.2.4", asset_name="Codexio.dmg", manifest_key="macos",
                                 architecture="arm64").sha256 == file_sha256(dmg)
    # Building Windows last updates its fields without erasing the Mac entry.
    exe.write_bytes(b"MZupdated windows fixture")
    assert build_manifest("windows", exe, "0.2.5", windows_manifest, merged_manifest).returncode == 0
    windows = json.loads(windows_manifest.read_text(encoding="utf-8"))
    assert windows["macos"] == merged["macos"]
    assert windows["version"] == "0.2.5" and windows["sha256"] == file_sha256(exe)
    # Rebuilding Mac can safely read and write the same shared manifest.
    dmg.write_bytes(b"rebuilt DMG fixture")
    assert build_manifest("macos", dmg, "0.2.5", windows_manifest, windows_manifest).returncode == 0
    rebuilt = json.loads(windows_manifest.read_text(encoding="utf-8"))
    assert rebuilt["sha256"] == windows["sha256"]
    assert rebuilt["macos"]["sha256"] == file_sha256(dmg)


def test_invalid_base_preserves_previous_manifest(tmp_path):
    base = tmp_path / "base.json"
    base.write_text('{"version":"0.2.4","url":"https://example.com/bad.exe"}', encoding="utf-8")
    dmg = tmp_path / "Codexio.dmg"
    dmg.write_bytes(b"DMG fixture")
    output = tmp_path / "latest.json"
    output.write_text("previous manifest", encoding="utf-8")
    assert build_manifest("macos", dmg, "0.2.4", output, base).returncode != 0
    assert output.read_text(encoding="utf-8") == "previous manifest"
