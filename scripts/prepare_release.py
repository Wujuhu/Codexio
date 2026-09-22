"""Archive a complete release only after the user confirms its version."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from codexio.app_archive import APP_ARCHIVE_NAME
from codexio.updates import UpdateError, version_tuple
from update_manifest import read_manifest
from verify_release import verify_release


def prepare_release(version, windows, macos, *, root=ROOT, platform="both", base=None):
    version = ".".join(map(str, version_tuple(version)))
    destination = root / "release" / version
    if destination.exists():
        raise UpdateError(f"正式版本目录已存在，不自动覆盖：{destination}")
    manifest = read_manifest(base or windows / "latest.json")
    manifest["macos"] = read_manifest(macos / "latest.json")["macos"]
    staging = root / "build/staging/release" / uuid.uuid4().hex
    staging.mkdir(parents=True)
    try:
        if platform == "both":
            shutil.copy2(windows / "Codexio.exe", staging / "Codexio.exe")
        shutil.copy2(macos / APP_ARCHIVE_NAME, staging / APP_ARCHIVE_NAME)
        (staging / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_release(staging, version, platform=platform)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def main():
    parser = argparse.ArgumentParser(description="仅在确认发布后，将两端已验证开发包归档到 release/<确认的版本号>。")
    parser.add_argument("--version", required=True, help="用户明确确认的发布版本；必须与两端程序一致")
    parser.add_argument("--windows", type=Path, default=ROOT / "build/dev/windows")
    parser.add_argument("--macos", type=Path, default=ROOT / "build/dev/macos")
    parser.add_argument("--platform", choices=("both", "macos"), default="both", help="仅在用户明确要求时选择单独发布 Mac")
    parser.add_argument("--base", type=Path, help="已发布的合并清单；单独发布 Mac 时保留其中 Windows 字段")
    args = parser.parse_args()
    print(prepare_release(args.version, args.windows, args.macos, platform=args.platform, base=args.base))


if __name__ == "__main__":
    main()
