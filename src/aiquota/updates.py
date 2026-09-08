"""Read stable GitHub releases and download verified Windows updates."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from aiquota import __version__

REPOSITORY = "Wujuhu/AIQuota"
RELEASES_URL = "https://github.com/" + REPOSITORY + "/releases"
LATEST_API = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
LATEST_MANIFEST = "https://github.com/" + REPOSITORY + "/releases/latest/download/latest.json"
EXE_NAME = "AIQuota.exe"
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
_VERSION = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_DIGEST = re.compile(r"sha256:([0-9a-fA-F]{64})\Z")


class UpdateError(Exception):
    pass


class UpdateCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int
    notes: str = ""


def version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value) if isinstance(value, str) and len(value) <= 18 else None
    if match is None:
        raise UpdateError("版本号必须为三段数字，例如 0.1.2")
    numbers = tuple(int(part) for part in match.groups())
    if any(part > 65535 for part in numbers):
        raise UpdateError("版本号超出 Windows 支持的范围")
    return numbers


def release_from_json(data: dict, current_version: str = __version__) -> Release | None:
    if not isinstance(data, dict):
        raise UpdateError("GitHub 返回了无效的版本信息")
    if data.get("draft") or data.get("prerelease"):
        return None
    tag = data.get("tag_name", "")
    parsed = version_tuple(tag)
    if parsed <= version_tuple(current_version):
        return None
    assets = data.get("assets")
    matches = [item for item in assets if isinstance(item, dict) and item.get("name") == EXE_NAME] if isinstance(assets, list) else []
    if len(matches) != 1 or matches[0].get("state") != "uploaded":
        raise UpdateError("新版尚未上传完整的 AIQuota.exe")
    asset = matches[0]
    expected_url = "https://github.com/%s/releases/download/%s/%s" % (REPOSITORY, tag, EXE_NAME)
    if asset.get("browser_download_url") != expected_url:
        raise UpdateError("更新文件不属于指定的 GitHub 发布版本")
    digest = _DIGEST.fullmatch(str(asset.get("digest") or ""))
    if digest is None:
        raise UpdateError("更新附件缺少 SHA-256 校验值，请重新上传附件")
    size = asset.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 2 <= size <= MAX_DOWNLOAD_BYTES:
        raise UpdateError("更新文件大小无效")
    return Release(".".join(map(str, parsed)), expected_url, digest.group(1).lower(), size,
                   str(data.get("body") or "")[:4000])


def _request(url: str) -> Request:
    return Request(url, headers={"Accept": "application/json" if url in (LATEST_API, LATEST_MANIFEST) else "application/octet-stream",
                                 "User-Agent": "AIQuota/" + __version__, "Cache-Control": "no-cache"})


def release_from_manifest(data: dict, current_version: str = __version__) -> Release | None:
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式无效")
    parsed = version_tuple(data.get("version", ""))
    tag = "v" + ".".join(map(str, parsed))
    return release_from_json({
        "tag_name": tag, "body": data.get("notes", ""), "assets": [{
            "name": EXE_NAME, "state": "uploaded", "browser_download_url": data.get("url"),
            "digest": "sha256:" + str(data.get("sha256", "")), "size": data.get("size"),
        }],
    }, current_version)


def fetch_release(current_version: str = __version__) -> Release | None:
    try:
        # A release attachment avoids the anonymous GitHub API rate limit.
        with urlopen(_request(LATEST_MANIFEST), timeout=20) as response:
            if urlsplit(response.geturl()).scheme != "https":
                raise UpdateError("更新清单必须通过 HTTPS 获取")
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise UpdateError("GitHub 返回的版本信息过大")
        return release_from_manifest(json.loads(payload.decode("utf-8-sig")), current_version)
    except HTTPError as exc:
        if exc.code == 404:
            return None  # A new public repository may not have a release yet.
        if exc.code in (403, 429):
            raise UpdateError("GitHub 暂时限制请求，请稍后重试") from exc
        raise UpdateError("GitHub 请求失败（HTTP %s）" % exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateError("无法连接 GitHub，请检查网络后重试") from exc
    except (ValueError, UnicodeError) as exc:
        raise UpdateError("GitHub 返回了无效的版本信息") from exc


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_release(release: Release, target: Path, cancel: threading.Event,
                     progress: Callable[[int], None] = lambda _value: None) -> Path:
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    received = 0
    try:
        if cancel.is_set():
            raise UpdateCancelled("已取消更新")
        with urlopen(_request(release.url), timeout=20) as response, partial.open("xb") as output:
            if urlsplit(response.geturl()).scheme != "https":
                raise UpdateError("更新下载必须使用 HTTPS")
            previous = -1
            while True:
                if cancel.is_set():
                    raise UpdateCancelled("已取消更新")
                block = response.read(256 * 1024)
                if not block:
                    break
                received += len(block)
                if received > release.size:
                    raise UpdateError("更新文件大小与发布信息不一致")
                digest.update(block)
                output.write(block)
                percent = received * 100 // release.size
                if percent != previous:
                    progress(percent)
                    previous = percent
        if received != release.size or digest.hexdigest() != release.sha256:
            raise UpdateError("更新文件校验失败，已保留当前版本")
        with partial.open("rb") as check:
            if check.read(2) != b"MZ":
                raise UpdateError("更新文件不是有效的 Windows 程序")
        if cancel.is_set():
            raise UpdateCancelled("已取消更新")
        partial.replace(target)
        return target
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateError("下载未完成，请检查网络和磁盘空间后重试") from exc
    finally:
        partial.unlink(missing_ok=True)
