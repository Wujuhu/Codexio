"""Publish a confirmed Apple release containing the Mac ZIP, iOS IPA and manifest.

This command is intentionally inert unless --confirm-publish is supplied. It
never creates or overwrites an existing local formal release directory.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from codexio import __version__
from codexio.app_archive import APP_ARCHIVE_NAME
from codexio.updates import UpdateError, file_sha256, version_tuple
from verify_release import verify_release
from ios_release import IPA_NAME, source_digest, validate_ipa

REPOSITORY = "Wujuhu/Codexio"
WORKFLOW = "release-windows.yml"
APPLE_ASSETS = (APP_ARCHIVE_NAME, IPA_NAME, "latest.json")


def run(*args, capture=False, check=True):
    result = subprocess.run([str(value) for value in args], cwd=ROOT, text=True,
                            encoding="utf-8", capture_output=capture, check=False)
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise UpdateError("命令执行失败：%s%s" % (" ".join(map(str, args)), ("\n" + detail) if detail else ""))
    return result


def output(*args):
    return run(*args, capture=True).stdout.strip()


def gh_release(tag):
    result = run("gh", "release", "view", tag, "--repo", REPOSITORY,
                 "--json", "isDraft,tagName,name,body,targetCommitish,assets,url", capture=True, check=False)
    if result.returncode:
        return None
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise UpdateError("GitHub Release 返回了无效数据") from exc
    return value if isinstance(value, dict) else None


def remote_ref(ref):
    result = run("git", "ls-remote", "origin", ref, capture=True, check=False)
    if result.returncode:
        raise UpdateError("无法读取远程 Git 引用：" + ref)
    line = result.stdout.strip().splitlines()
    return line[0].split()[0] if line else None


def validate_notes(release, notes=""):
    if str(release.get("body") or "").strip() != notes.strip():
        raise UpdateError("Release 正文与本次确认的内容不一致")


def write_notes(staging, notes):
    path = staging / "release-notes.txt"
    path.write_text(notes, encoding="utf-8")
    return path


def validate_mac(version, staging):
    mac = ROOT / "build/dev/macos"
    app = mac / "Codexio.app"
    package = mac / APP_ARCHIVE_NAME
    manifest = mac / "latest.json"
    for path in (app, package, manifest):
        if not path.exists():
            raise UpdateError("缺少 Mac 开发包：" + str(path))
    with (app / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    if info.get("CFBundleShortVersionString") != version or info.get("CFBundleVersion") != version:
        raise UpdateError("Mac APP 版本与确认发布版本不一致")
    run("codesign", "--verify", "--deep", "--strict", app)
    mac_stage = staging / "macos"
    mac_stage.mkdir(parents=True)
    shutil.copy2(package, mac_stage / APP_ARCHIVE_NAME)
    shutil.copy2(manifest, mac_stage / "latest.json")
    verify_release(mac_stage, version, platform="macos")
    return mac_stage


def prepare_ios(version, directory):
    ios = ROOT / "build/dev/ios"
    package = ios / IPA_NAME
    info = json.loads((ios / "build-info.json").read_text(encoding="utf-8"))
    ios_version = (ROOT / "ios/VERSION").read_text(encoding="utf-8").strip()
    if (info.get("version") != ios_version or info.get("source_sha256") != source_digest(ROOT)
            or info.get("size") != package.stat().st_size or info.get("sha256") != file_sha256(package)
            or info.get("signed") is not False):
        raise UpdateError("iOS 开发包缺失、过期或校验不符，请先运行 scripts/build_ios.py")
    bundle = validate_ipa(package, ios_version)
    shutil.copy2(package, directory / IPA_NAME)
    manifest_path = directory / "latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["ios"] = {
        "version": ios_version, "url": f"https://github.com/{REPOSITORY}/releases/download/v{version}/{IPA_NAME}",
        "sha256": info["sha256"], "size": info["size"], "architecture": "arm64", "signed": False,
        "minimum_ios": bundle.get("MinimumOSVersion"),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verify_release(directory, version, platform="macos", include_ios=True)


def validate_repository(version, include_windows=False):
    mac_version = (ROOT / "macos/VERSION").read_text(encoding="utf-8").strip()
    if mac_version != version:
        raise UpdateError("Mac 源码版本 %s 与确认发布版本 %s 不一致" % (mac_version, version))
    if include_windows and __version__ != version:
        raise UpdateError("Windows 源码版本与本次跨平台发布版本不一致")
    if output("git", "branch", "--show-current") != "main":
        raise UpdateError("正式发布只能从 main 分支执行")
    tracked = output("git", "status", "--porcelain", "--untracked-files=no")
    if tracked:
        raise UpdateError("存在尚未提交的已跟踪文件，未开始发布")
    run("gh", "auth", "status", "--hostname", "github.com")
    return output("git", "rev-parse", "HEAD")


def ensure_remote_main(commit):
    remote = remote_ref("refs/heads/main")
    if remote != commit:
        run("git", "push", "origin", "main")
        remote = remote_ref("refs/heads/main")
    if remote != commit:
        raise UpdateError("远程 main 未指向本次确认的提交")


def validate_draft(release, tag, commit, staging, mac_stage, include_windows=False, notes=""):
    if (not release or release.get("isDraft") is not True or release.get("tagName") != tag
            or release.get("name") != tag or release.get("targetCommitish") != commit):
        raise UpdateError("现有同版本 Release 不是可继续的草稿")
    validate_notes(release, notes)
    names = sorted(str(item.get("name") or "") for item in release.get("assets") or [])
    allowed = APPLE_ASSETS + (("Codexio.exe",) if include_windows else ())
    if any(name not in allowed for name in names):
        raise UpdateError("草稿 Release 的附件集合不符合当前发布约定")
    downloaded = staging / "draft-macos"
    downloaded.mkdir()
    run("gh", "release", "upload", tag, "--repo", REPOSITORY, "--clobber",
        *(mac_stage / name for name in APPLE_ASSETS))
    run("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", downloaded,
        "--pattern", APP_ARCHIVE_NAME, "--pattern", IPA_NAME, "--pattern", "latest.json", "--clobber")
    verify_release(downloaded, tag.removeprefix("v"), platform="macos", include_ios=True)
    for name in APPLE_ASSETS:
        if file_sha256(downloaded / name) != file_sha256(mac_stage / name):
            raise UpdateError("草稿中的 Mac 附件与本机已验证开发包不一致：" + name)


def create_draft(tag, commit, mac_stage, staging, notes=""):
    if gh_release(tag) is not None or remote_ref("refs/tags/" + tag) is not None:
        raise UpdateError("同版本 Tag 或 Release 已存在，不自动覆盖")
    local_tag = run("git", "show-ref", "--verify", "--quiet", "refs/tags/" + tag, check=False)
    if local_tag.returncode == 0:
        raise UpdateError("本地同版本 Tag 已存在，不自动覆盖")
    notes_path = write_notes(staging, notes)
    run("gh", "release", "create", tag, "--repo", REPOSITORY, "--target", commit,
        "--title", tag, "--draft", "--notes-file", notes_path,
        *(mac_stage / name for name in APPLE_ASSETS))
    release = gh_release(tag)
    if not release or release.get("isDraft") is not True or release.get("targetCommitish") != commit:
        raise UpdateError("未能确认 GitHub 草稿 Release")
    validate_notes(release, notes)
    return release


def dispatch_and_wait(version, commit):
    request_id = uuid.uuid4().hex
    run("gh", "workflow", "run", WORKFLOW, "--repo", REPOSITORY, "--ref", "main",
        "-f", "version=" + version, "-f", "commit=" + commit, "-f", "request_id=" + request_id)
    deadline = time.monotonic() + 120
    selected = None
    while time.monotonic() < deadline:
        raw = output("gh", "run", "list", "--repo", REPOSITORY, "--workflow", WORKFLOW,
                     "--event", "workflow_dispatch", "--limit", "30",
                     "--json", "databaseId,displayTitle,headSha,status,conclusion,url")
        try:
            rows = json.loads(raw)
        except ValueError as exc:
            raise UpdateError("无法解析 GitHub Actions 运行列表") from exc
        selected = next((row for row in rows if request_id in str(row.get("displayTitle") or "")
                         and row.get("headSha") == commit), None)
        if selected:
            break
        time.sleep(3)
    if not selected:
        raise UpdateError("未找到刚触发的 Windows 发布工作流")
    print("Windows 构建：" + str(selected.get("url") or selected["databaseId"]))
    run("gh", "run", "watch", str(selected["databaseId"]), "--repo", REPOSITORY, "--exit-status")


def verify_published(tag, commit=None, include_windows=False, include_ios=True, notes=""):
    release = gh_release(tag)
    if not release or release.get("isDraft") is not False or release.get("tagName") != tag or release.get("name") != tag:
        raise UpdateError("未得到有效的正式 Release")
    validate_notes(release, notes)
    names = sorted(str(item.get("name") or "") for item in release.get("assets") or [])
    expected = (APP_ARCHIVE_NAME, "latest.json") + ((IPA_NAME,) if include_ios else ()) + (("Codexio.exe",) if include_windows else ())
    if names != sorted(expected):
        raise UpdateError("正式 Release 附件与本次确认的平台不一致")
    if commit is not None and remote_ref("refs/tags/" + tag) != commit:
        raise UpdateError("正式 Release Tag 未指向确认提交")
    return release


def finalize_mac_draft(tag, commit, staging, mac_stage, notes=""):
    release = gh_release(tag)
    if (not release or release.get("isDraft") is not True or release.get("tagName") != tag
            or release.get("name") != tag or release.get("targetCommitish") != commit):
        raise UpdateError("Mac 草稿状态不符合发布条件")
    validate_notes(release, notes)
    if sorted(item.get("name") for item in release.get("assets") or []) != sorted(APPLE_ASSETS):
        raise UpdateError("草稿必须且只能有 Mac ZIP、iOS IPA 与 latest.json")
    downloaded = staging / "verified-mac-draft"
    downloaded.mkdir()
    run("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", downloaded)
    verify_release(downloaded, tag.removeprefix("v"), platform="macos", include_ios=True)
    for name in APPLE_ASSETS:
        if file_sha256(downloaded / name) != file_sha256(mac_stage / name):
            raise UpdateError("草稿附件与已验证本地文件不一致：" + name)
    tag_commit = remote_ref("refs/tags/" + tag)
    if tag_commit is not None and tag_commit != commit:
        raise UpdateError("同名 Tag 已被其他提交占用")
    run("gh", "release", "edit", tag, "--repo", REPOSITORY, "--draft=false", "--latest")


def sync_local_release(version, tag, staging, include_windows=False, include_ios=True):
    destination = ROOT / "release" / version
    if destination.exists():
        raise UpdateError("正式版本目录已存在，不自动覆盖：" + str(destination))
    final = staging / "final"
    final.mkdir()
    run("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", final)
    verify_release(final, version, platform="both" if include_windows else "macos", include_ios=include_ios)
    destination.parent.mkdir(parents=True, exist_ok=True)
    final.rename(destination)
    run("git", "fetch", "origin", "tag", tag)
    return destination


def publish(version, *, resume_draft=False, sync_only=False, include_windows=False, notes=""):
    version = ".".join(map(str, version_tuple(version)))
    tag = "v" + version
    if (ROOT / "release" / version).exists():
        raise UpdateError("正式版本目录已存在，不自动覆盖：" + str(ROOT / "release" / version))
    staging = ROOT / "build/staging/release-coordinator" / uuid.uuid4().hex
    staging.mkdir(parents=True)
    try:
        commit = validate_repository(version, include_windows)
        if sync_only:
            previous = gh_release(tag)
            has_ios = any(asset.get("name") == IPA_NAME for asset in (previous or {}).get("assets") or [])
            verify_published(tag, commit, include_windows, include_ios=has_ios, notes=notes)
            return sync_local_release(version, tag, staging, include_windows, include_ios=has_ios)
        mac_stage = validate_mac(version, staging)
        prepare_ios(version, mac_stage)
        existing = gh_release(tag)
        if existing is not None:
            if not resume_draft:
                raise UpdateError("同版本 Release 已存在；仅可显式使用 --resume-draft 继续匹配的草稿")
            if (existing.get("isDraft") is not True or existing.get("tagName") != tag
                    or existing.get("name") != tag):
                raise UpdateError("现有同版本 Release 不是可继续的草稿")
            validate_notes(existing, notes)
            old_target = str(existing.get("targetCommitish") or "")
            if old_target != commit:
                if (len(old_target) != 40 or any(character not in "0123456789abcdef" for character in old_target.lower())
                        or run("git", "merge-base", "--is-ancestor", old_target, commit, check=False).returncode != 0):
                    raise UpdateError("草稿 Release 的目标不是当前提交的可安全前移祖先")
        elif remote_ref("refs/tags/" + tag) is not None:
            raise UpdateError("同版本远程 Tag 已存在，不自动覆盖")
        elif run("git", "show-ref", "--verify", "--quiet", "refs/tags/" + tag, check=False).returncode == 0:
            raise UpdateError("本地同版本 Tag 已存在，不自动覆盖")
        ensure_remote_main(commit)
        if existing is not None:
            if existing.get("targetCommitish") != commit:
                notes_path = write_notes(staging, notes)
                run("gh", "release", "edit", tag, "--repo", REPOSITORY, "--target", commit,
                    "--title", tag, "--notes-file", notes_path, "--draft=true")
                existing = gh_release(tag)
            validate_draft(existing, tag, commit, staging, mac_stage, include_windows, notes=notes)
        else:
            create_draft(tag, commit, mac_stage, staging, notes=notes)
        if include_windows:
            dispatch_and_wait(version, commit)
        else:
            finalize_mac_draft(tag, commit, staging, mac_stage, notes=notes)
        release = verify_published(tag, commit, include_windows, notes=notes)
        destination = sync_local_release(version, tag, staging, include_windows)
        print("已发布：" + str(release.get("url") or tag))
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--confirm-publish", action="store_true",
                        help="确认本次命令获准推送 main、创建 Tag 并发布 GitHub Release")
    parser.add_argument("--resume-draft", action="store_true",
                        help="继续同一版本、同一提交且附件匹配的失败草稿")
    parser.add_argument("--sync-only", action="store_true",
                        help="只下载并核验本平台已发布附件，补齐本地 release 归档")
    parser.add_argument("--include-windows", action="store_true",
                        help="仅在用户明确要求本次也发布 Windows 时使用；默认不触发 Windows CI")
    parser.add_argument("--notes-file", type=Path,
                        help="仅在用户明确要求正文时读取 UTF-8 说明文件；默认仍为空正文")
    args = parser.parse_args()
    if not args.confirm_publish and not args.sync_only:
        parser.error("发布必须显式提供 --confirm-publish")
    try:
        notes = args.notes_file.read_text(encoding="utf-8") if args.notes_file else ""
        print(publish(args.version, resume_draft=args.resume_draft, sync_only=args.sync_only,
                      include_windows=args.include_windows, notes=notes))
    except (OSError, ValueError, UpdateError, subprocess.SubprocessError) as exc:
        parser.exit(1, "发布未完成：%s\n" % exc)


if __name__ == "__main__":
    main()
