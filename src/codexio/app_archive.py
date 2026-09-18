"""Portable validation and extraction of the signed Mac application archive."""
from __future__ import annotations

import plistlib
import posixpath
import shutil
import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

from codexio.updates import UpdateError, version_tuple

APP_ARCHIVE_NAME = "Codexio.app.zip"
BUNDLE_NAME = "Codexio.app"
BUNDLE_ID = "com.wujuhu.codexio"
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024


def _members(archive, version):
    entries = archive.infolist()
    if not entries or len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
        raise UpdateError("APP 压缩包大小或文件数量无效")
    seen, links, files = set(), set(), set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        name = unicodedata.normalize("NFD", str(path)).casefold()
        kind = stat.S_IFMT(entry.external_attr >> 16)
        if (not path.parts or path.parts[0] != BUNDLE_NAME or path.is_absolute()
                or ".." in path.parts or "\\" in entry.filename
                or "\x00" in entry.orig_filename or name in seen or entry.flag_bits & 1
                or kind not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK)):
            raise UpdateError("APP 压缩包包含无效或重复的路径")
        seen.add(name)
        if kind == stat.S_IFLNK:
            if entry.file_size > 4096:
                raise UpdateError("APP 压缩包链接无效")
            target = archive.read(entry).decode("utf-8")
            resolved = posixpath.normpath(posixpath.join(str(path.parent), target))
            if (not target or "\x00" in target or "\\" in target or target.startswith("/")
                    or not (resolved == BUNDLE_NAME or resolved.startswith(BUNDLE_NAME + "/"))):
                raise UpdateError("APP 压缩包链接越界")
            links.add(name)
        elif not entry.is_dir():
            files.add(name)
    for entry in entries:
        for parent in PurePosixPath(entry.filename).parents:
            name = unicodedata.normalize("NFD", str(parent)).casefold()
            if name in links or name in files:
                raise UpdateError("APP 压缩包路径经过文件或链接")
    plist = archive.getinfo(BUNDLE_NAME + "/Contents/Info.plist")
    binary = archive.getinfo(BUNDLE_NAME + "/Contents/MacOS/Codexio")
    if plist.file_size > 1024 * 1024 or any(stat.S_ISLNK(e.external_attr >> 16) or e.is_dir()
                                         for e in (plist, binary)):
        raise UpdateError("APP 应用信息无效")
    info = plistlib.loads(archive.read(plist))
    if not isinstance(info, dict):
        raise UpdateError("APP 应用信息无效")
    actual = info.get("CFBundleShortVersionString")
    version_tuple(actual)
    if (info.get("CFBundleIdentifier") != BUNDLE_ID or info.get("CFBundleExecutable") != "Codexio"
            or info.get("CFBundleVersion") != actual or (version is not None and actual != version)):
        raise UpdateError("APP 版本或标识与更新清单不一致")
    return entries


def validate_app_archive(package: Path, version=None):
    try:
        with zipfile.ZipFile(package) as archive:
            _members(archive, version)
            if archive.testzip() is not None:
                raise UpdateError("APP 压缩包内容损坏")
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, plistlib.InvalidFileException) as exc:
        raise UpdateError("APP 压缩包无效") from exc


def extract_app_archive(package: Path, destination: Path, version):
    # Create links last: no archive member can write through a symlink.
    destination.mkdir()
    try:
        with zipfile.ZipFile(package) as archive:
            entries = _members(archive, version)
            links = []
            for entry in entries:
                target = destination.joinpath(*PurePosixPath(entry.filename).parts[1:])
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    links.append((target, archive.read(entry).decode("utf-8")))
                    continue
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod((mode & 0o777) or 0o644)
            for target, link in links:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
            for target, _ in links:
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise UpdateError("APP 压缩包链接越界")
    except Exception as exc:
        shutil.rmtree(destination)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("APP 解压失败，已保留当前版本") from exc
