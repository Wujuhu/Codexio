"""Verify the three delivery files in release/<version> before uploading them."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codexio.updates import UpdateError, file_sha256, release_from_manifest, version_tuple


def verify_release(directory: Path, version: str) -> None:
    version = ".".join(map(str, version_tuple(version)))
    manifest = json.loads((directory / "latest.json").read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise UpdateError("更新清单格式无效")
    for name, key in (("Codexio.exe", None), ("Codexio.dmg", "macos")):
        release = release_from_manifest(manifest, "0.0.0", asset_name=name, manifest_key=key)
        if release is None or release.version != version:
            raise UpdateError(f"{name} 的清单版本与交付版本 {version} 不一致")
        asset = directory / name
        if not asset.is_file():
            raise UpdateError(f"缺少 {asset}，请先归集另一平台的同版本安装包")
        if asset.stat().st_size != release.size or file_sha256(asset) != release.sha256:
            raise UpdateError(f"{name} 与 latest.json 的大小或 SHA-256 不一致")
    with (directory / "Codexio.exe").open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise UpdateError("Codexio.exe 不是 Windows 可执行文件")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    version = ".".join(map(str, version_tuple(args.version)))
    directory = args.directory or ROOT / "release" / version
    verify_release(directory, version)
    print(f"Verified {directory}: Codexio.exe, Codexio.dmg, latest.json")


if __name__ == "__main__":
    main()
