"""Verify the three delivery files in release/<version> before uploading them."""
from __future__ import annotations

import argparse
import json
import plistlib
import stat
import struct
import zipfile
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codexio.app_archive import APP_ARCHIVE_NAME, validate_app_archive
from codexio.updates import UpdateError, file_sha256, release_from_manifest, version_tuple
from codexio.versions import AppVersion

LEGACY_IPA_NAME = "Codexio.ipa"


def validate_legacy_ipa(path: Path, version: str) -> dict:
    parsed = AppVersion.parse(version)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("IPA contains duplicate paths")
        for entry in archive.infolist():
            relative = PurePosixPath(entry.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename or stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                raise ValueError("IPA contains an unsafe or encrypted entry")
        if archive.testzip() is not None:
            raise ValueError("IPA CRC verification failed")
        info = plistlib.loads(archive.read("Payload/Codexio.app/Info.plist"))
        if (info.get("CFBundleShortVersionString") != parsed.numeric or info.get("CFBundleVersion") != parsed.numeric
                or info.get("CodexioVersion",info.get("CFBundleShortVersionString")) != version
                or info.get("CFBundleIdentifier") != "com.wujuhu.codexio.ios"
                or info.get("CFBundleExecutable") != "Codexio"
                or "iPhoneOS" not in info.get("CFBundleSupportedPlatforms", [])):
            raise ValueError("IPA bundle identity or version does not match")
        with archive.open("Payload/Codexio.app/Codexio") as stream:
            header = stream.read(8)
        if len(header) != 8 or struct.unpack("<II", header) != (0xFEEDFACF, 0x0100000C):
            raise ValueError("IPA executable must be device arm64 Mach-O")
    return info


def verify_release(directory: Path, version: str, *, platform="both", include_ios=None) -> None:
    version = ".".join(map(str, version_tuple(version)))
    manifest = json.loads((directory / "latest.json").read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise UpdateError("更新清单格式无效")
    assets = (("Codexio.exe", None), (APP_ARCHIVE_NAME, "macos")) if platform == "both" else ((APP_ARCHIVE_NAME, "macos"),)
    if include_ios is None:
        include_ios = (directory / LEGACY_IPA_NAME).is_file()
    if not include_ios and "ios" in manifest:
        raise UpdateError("桌面发布清单不得包含已停用的 ios 字段")
    expected = {name for name, _ in assets} | {"latest.json"}
    if include_ios:
        expected.add(LEGACY_IPA_NAME)
    if {path.name for path in directory.iterdir()} != expected:
        raise UpdateError("正式版本目录附件与本次发布的平台不一致")
    if platform == "macos" and "version" in manifest:
        release_from_manifest(manifest, "0.0.0")
    for name, key in assets:
        release = release_from_manifest(manifest, "0.0.0", asset_name=name, manifest_key=key)
        if release is None or release.version != version:
            raise UpdateError(f"{name} 的清单版本与交付版本 {version} 不一致")
        asset = directory / name
        if not asset.is_file():
            raise UpdateError(f"缺少 {asset}，请先归集另一平台的同版本安装包")
        if asset.stat().st_size != release.size or file_sha256(asset) != release.sha256:
            raise UpdateError(f"{name} 与 latest.json 的大小或 SHA-256 不一致")
    validate_app_archive(directory / APP_ARCHIVE_NAME, version)
    if include_ios:
        ios = manifest.get("ios")
        if not isinstance(ios, dict) or ios.get("url") != f"https://github.com/Wujuhu/Codexio/releases/download/v{version}/{LEGACY_IPA_NAME}":
            raise UpdateError("缺少有效的 iOS 发布清单")
        package = directory / LEGACY_IPA_NAME
        if package.stat().st_size != ios.get("size") or file_sha256(package) != ios.get("sha256"):
            raise UpdateError("IPA 大小或 SHA-256 不匹配")
        validate_legacy_ipa(package, str(ios.get("version") or ""))
    if platform == "both":
        with (directory / "Codexio.exe").open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise UpdateError("Codexio.exe 不是 Windows 可执行文件")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--platform", choices=("both", "macos"), default="both")
    parser.add_argument("--include-ios", action="store_true", default=None, help="兼容核验历史 IPA；默认按归档的实际附件集合判断")
    args = parser.parse_args()
    version = ".".join(map(str, version_tuple(args.version)))
    directory = args.directory or ROOT / "release" / version
    verify_release(directory, version, platform=args.platform, include_ios=args.include_ios)
    print(f"Verified {directory}: " + ", ".join(sorted(path.name for path in directory.iterdir())))


if __name__ == "__main__":
    main()
