from __future__ import annotations

import plistlib
import stat
import sys
import zipfile

import pytest

from codexio.app_archive import extract_app_archive, validate_app_archive
from codexio.updates import UpdateError


def make_archive(path, additions=(), version="0.2.4"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Codexio.app/Contents/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": "com.wujuhu.codexio", "CFBundleExecutable": "Codexio",
            "CFBundleShortVersionString": version, "CFBundleVersion": version}))
        binary = zipfile.ZipInfo("Codexio.app/Contents/MacOS/Codexio")
        binary.external_attr = (stat.S_IFREG | 0o755) << 16
        archive.writestr(binary, b"binary")
        for name, value, mode in additions:
            entry = zipfile.ZipInfo(name)
            entry.external_attr = mode << 16
            archive.writestr(entry, value)


@pytest.mark.skipif(sys.platform == "win32", reason="macOS executable permissions and symlinks")
def test_extract_preserves_executable_permissions_and_internal_links(tmp_path):
    package = tmp_path / "app.zip"
    make_archive(package, [("Codexio.app/Contents/Resources/linked-binary", "../MacOS/Codexio", stat.S_IFLNK | 0o777)])
    validate_app_archive(package, "0.2.4")
    target = tmp_path / "新 应用.app"
    extract_app_archive(package, target, "0.2.4")
    assert (target / "Contents/MacOS/Codexio").stat().st_mode & 0o111
    link = target / "Contents/Resources/linked-binary"
    assert link.is_symlink() and link.read_bytes() == b"binary"


@pytest.mark.parametrize("members", [
    [("../escape", "bad", stat.S_IFREG)],
    [("Codexio.app/../../escape", "bad", stat.S_IFREG)],
    [("/Codexio.app/escape", "bad", stat.S_IFREG)],
    [("Codexio.app/Contents/link", "../../../escape", stat.S_IFLNK)],
    [("Codexio.app/Contents/link", "/tmp/escape", stat.S_IFLNK)],
    [("Codexio.app/Contents/link", "MacOS", stat.S_IFLNK),
     ("Codexio.app/Contents/link/Codexio", "bad", stat.S_IFREG)],
    [("Codexio.app/Contents/macos/codexio", "duplicate", stat.S_IFREG)],
    [("Other.app/file", "bad", stat.S_IFREG)],
])
def test_rejects_unsafe_archives_without_writing_outside_app(tmp_path, members):
    package = tmp_path / "app.zip"
    make_archive(package, members)
    target = tmp_path / "pending.app"
    with pytest.raises(UpdateError):
        extract_app_archive(package, target, "0.2.4")
    assert not target.exists()
    assert {p.name for p in tmp_path.iterdir()} == {"app.zip"}


def test_wrong_version_and_corrupt_archives_leave_existing_app_untouched(tmp_path):
    package = tmp_path / "app.zip"
    make_archive(package, version="0.2.3")
    with pytest.raises(UpdateError):
        validate_app_archive(package, "0.2.4")
    package.write_bytes(b"not a zip")
    with pytest.raises(UpdateError):
        extract_app_archive(package, tmp_path / "pending.app", "0.2.4")
    assert not (tmp_path / "pending.app").exists()
