"""Merge one freshly built asset into the shared, legacy-compatible latest.json."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codexio.updates import LATEST_MANIFEST, REPOSITORY, UpdateError, file_sha256, release_from_manifest, version_tuple


def read_manifest(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式无效")
    return data


def load_base(system, explicit=None, *, version=None):
    if explicit:
        return read_manifest(explicit)
    # Prefer a local shared manifest that already includes the other platform.
    candidates = [ROOT / "release" / version / "latest.json"] if version else []
    # Legacy locations are read only, to support the first build after migration.
    candidates += [ROOT / "dist/latest.json", ROOT / "build/macos/latest.json"]
    for path in candidates:
        if path.exists():
            data = read_manifest(path)
            if (system == "macos" and "version" in data) or (system == "windows" and "macos" in data):
                return data
    # The first Mac build on a new checkout preserves the published EXE metadata.
    try:
        request = Request(LATEST_MANIFEST, headers={"User-Agent": "Codexio-build", "Accept": "application/json"})
        with urlopen(request, timeout=20) as response:
            if urlsplit(response.geturl()).scheme != "https":
                raise UpdateError("更新清单必须通过 HTTPS 获取")
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise UpdateError("更新清单过大")
        data = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(data, dict):
            raise UpdateError("更新清单格式无效")
        return data
    except HTTPError as exc:
        if exc.code == 404 and system == "windows":
            return {}
        raise UpdateError("无法获取现有 latest.json，请用 --base 指定清单文件") from exc


def write_manifest(system, asset, version, output, *, base=None, architecture=None):
    version = ".".join(map(str, version_tuple(version)))
    name = "Codexio.dmg" if system == "macos" else "Codexio.exe"
    if asset.name != name:
        raise UpdateError("更新文件名必须为 " + name)
    data = load_base(system, base, version=version)
    if system == "macos":
        # Keep every existing Windows field intact, including its own version.
        release_from_manifest(data, "0.0.0")
    elif "macos" in data:
        release_from_manifest(data, "0.0.0", asset_name="Codexio.dmg", manifest_key="macos")
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
