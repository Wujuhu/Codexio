"""Merge one freshly built asset into the shared, legacy-compatible latest.json."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codexio.app_archive import APP_ARCHIVE_NAME
from codexio.updates import REPOSITORY, UpdateError, file_sha256, release_from_manifest, version_tuple


def read_manifest(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式无效")
    return data


def load_base(system, explicit=None, *, version=None):
    if explicit:
        return read_manifest(explicit)
    # Development builds stay offline and prefer the other platform's current build.
    other = "windows" if system == "macos" else "macos"
    candidates = [ROOT / "build/dev" / other / "latest.json"]
    if version:
        candidates.append(ROOT / "release" / version / "latest.json")
    for path in candidates:
        if path.exists():
            data = read_manifest(path)
            entry = data if system == "macos" else data.get("macos", {})
            if not isinstance(entry, dict):
                raise UpdateError("平台更新清单格式无效")
            if entry.get("version") == version:
                return data
    return {}


def write_manifest(system, asset, version, output, *, base=None, architecture=None):
    version = ".".join(map(str, version_tuple(version)))
    name = APP_ARCHIVE_NAME if system == "macos" else "Codexio.exe"
    if asset.name != name:
        raise UpdateError("更新文件名必须为 " + name)
    data = load_base(system, base, version=version)
    if system == "macos":
        # Keep every existing Windows field intact, including its own version.
        if "version" in data:
            release_from_manifest(data, "0.0.0")
    elif "macos" in data:
        # An old release can serve as a base while Mac migrates from DMG to APP ZIP.
        if not isinstance(data["macos"], dict):
            raise UpdateError("平台更新清单格式无效")
        name_in_base = "Codexio.dmg" if str(data["macos"].get("url", "")).endswith("/Codexio.dmg") else APP_ARCHIVE_NAME
        release_from_manifest(data, "0.0.0", asset_name=name_in_base, manifest_key="macos")
    entry = dict(version=version, url=f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}",
                 sha256=file_sha256(asset), size=asset.stat().st_size)
    if system == "macos":
        entry["architecture"] = architecture or platform.machine()
        data["macos"] = entry
    else:
        data.update(entry, notes="Codexio " + version)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "macos"), required=True)
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base", type=Path, help="现有或另一平台构建的 latest.json")
    parser.add_argument("--architecture")
    args = parser.parse_args()
    write_manifest(args.platform, args.asset, args.version, args.output,
                   base=args.base, architecture=args.architecture)


if __name__ == "__main__":
    main()
