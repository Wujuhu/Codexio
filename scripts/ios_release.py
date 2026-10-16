"""Local iOS delivery validation, shared by the builder and release coordinator."""
import hashlib
import plistlib
import re
import stat
import struct
import sys
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from codexio.versions import AppVersion

IPA_NAME = "Codexio.ipa"


def desktop_version(root: Path) -> str:
    versions = []
    for platform in ("macos", "windows"):
        value = (root / platform / "VERSION").read_text(encoding="utf-8").strip()
        versions.append(AppVersion.parse(value))
    return str(max(versions))


def source_digest(root: Path) -> str:
    paths = sorted((root / "apple/shared").glob("*.swift")) + sorted((root / "ios/App").glob("*.swift"))
    paths += [root / name for name in ("ios/VERSION", "scripts/build_ios.py", "scripts/ios_release.py", "src/codexio/versions.py", "src/codexio/icons/wordmark.png", "src/codexio/icons/app-light.png")]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def validate_ipa(path: Path, version: str) -> dict:
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
