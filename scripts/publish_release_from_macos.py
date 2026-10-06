"""Publish verified Mac, iOS and local Windows artifacts after confirmation.

This command is intentionally inert unless --confirm-publish is supplied. It
never creates or overwrites an existing local formal release directory.
"""
from __future__ import annotations

import argparse
import base64
import json
import plistlib
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from codexio.app_archive import APP_ARCHIVE_NAME
from codexio.updates import UpdateError, file_sha256, release_from_manifest, version_tuple
from verify_release import verify_release
from ios_release import IPA_NAME, desktop_version, source_digest, validate_ipa

REPOSITORY = "Wujuhu/Codexio"
APPLE_ASSETS = (APP_ARCHIVE_NAME, IPA_NAME, "latest.json")
WINDOWS_HOSTS = ("192.168.1.16", "100.82.100.123")
WINDOWS_SSH_OPTIONS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                       "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                       "-o", "StrictHostKeyChecking=yes", "-o", "HostKeyAlias=192.168.1.16",
                       "-i", "/Users/wjh/.ssh/windows_ed25519")


def release_assets(include_windows):
    return APPLE_ASSETS + (("Codexio.exe",) if include_windows else ())


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


def platform_notes(notes):
    """Use the same fenced-code-aware H2 boundaries as iOS UpdateChecker."""
    sections = {}
    current = None
    fence = None
    for line in notes.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            token, tail = marker.groups()
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not tail.strip():
                fence = None
        elif fence is None:
            heading = re.match(r"^ {0,3}(#{1,2})(?:[ \t]+(.*)|$)", line)
            if heading:
                name = (heading.group(2) or "").strip().rstrip("#").strip().lower()
                canonical = {"mac": "Mac", "windows": "Windows", "ios": "iOS"}.get(name)
                if heading.group(1) != "##" or canonical is None:
                    raise UpdateError("Release 正文请使用 ## Mac、## Windows、## iOS 平台分段")
                if canonical in sections:
                    raise UpdateError("Release 正文包含重复平台段落：" + canonical)
                sections[canonical] = []
                current = canonical
                continue
        if current is None:
            if line.strip():
                raise UpdateError("Release 更新条目必须放在对应平台标题下面")
        else:
            sections[current].append(line)
    values = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    if not values or any(not value for value in values.values()) or fence is not None:
        raise UpdateError("Release 平台段落不能为空，Markdown 代码块必须完整")
    return values


def validate_ios_notes(sections, staging):
    if sections.get("iOS"):
        return
    # Mac-only changes may omit iOS notes when the carried IPA version has
    # already shipped. Compare the actual published manifest before any push.
    response = run("gh", "api", f"repos/{REPOSITORY}/releases/latest", capture=True, check=False)
    if response.returncode:
        raise UpdateError("无法确认已发布的 iOS 版本，请提供非空的 ## iOS 更新说明")
    latest = json.loads(response.stdout)
    tag = str(latest.get("tag_name") or "")
    if not tag.startswith("v"):
        raise UpdateError("最新正式 Release 的版本标记无效")
    version_tuple(tag[1:])
    directory = staging / "previous-ios-manifest"
    directory.mkdir()
    run("gh", "release", "download", tag, "--repo", REPOSITORY,
        "--pattern", "latest.json", "--dir", directory)
    manifest = json.loads((directory / "latest.json").read_text(encoding="utf-8-sig"))
    previous = str((manifest.get("ios") or {}).get("version") or "")
    current = (ROOT / "ios/VERSION").read_text(encoding="utf-8").strip()
    if not previous or version_tuple(current) > version_tuple(previous):
        raise UpdateError("iOS 升版必须提供非空的 ## iOS 更新说明")


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
    if ios_version != desktop_version(ROOT):
        raise UpdateError("iOS 版本未跟随 Mac/Windows 的最新版本，请先运行 scripts/build_ios.py")
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


def preserve_published_windows(directory, staging):
    """Carry the published Windows fields, never an unshipped development EXE."""
    published = json.loads(output("gh", "api", f"repos/{REPOSITORY}/releases/latest"))
    tag = str(published.get("tag_name") or "")
    if published.get("draft") or published.get("prerelease") or not tag.startswith("v"):
        raise UpdateError("无法确认上一份正式发布清单")
    version_tuple(tag[1:])
    previous = staging / "published-windows-manifest"
    previous.mkdir()
    run("gh", "release", "download", tag, "--repo", REPOSITORY,
        "--pattern", "latest.json", "--dir", previous)
    path = previous / "latest.json"
    asset = next((item for item in published.get("assets", []) if item.get("name") == "latest.json"), {})
    if (asset.get("state") != "uploaded" or asset.get("size") != path.stat().st_size
            or asset.get("digest") != "sha256:" + file_sha256(path)):
        raise UpdateError("上一份正式清单与 GitHub 附件校验不一致")
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if release_from_manifest(manifest, "0.0.0") is None:
        raise UpdateError("上一份正式清单缺少有效的 Windows 版本")
    destination = directory / "latest.json"
    candidate = json.loads(destination.read_text(encoding="utf-8"))
    for platform in ("macos", "ios"):
        if platform in candidate:
            manifest[platform] = candidate[platform]
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_windows(version, commit, directory):
    """Validate on the build host, then reuse or copy its exact verified EXE."""
    code = "\n".join((
        "import json, sys",
        "from pathlib import Path",
        "sys.path.insert(0, 'scripts')",
        "from append_windows_release import ROOT, run, validate_windows",
        f"if run('git', 'rev-parse', 'HEAD') != {commit!r}: raise RuntimeError('Windows checkout is not the confirmed commit')",
        "if run('git', 'status', '--porcelain'): raise RuntimeError('Windows checkout is not clean')",
        f"delivery = json.loads((ROOT / 'build/checks/v{version}/delivery.json').read_text(encoding='utf-8-sig'))",
        f"validate_windows({version!r}, Path(delivery['executable']), delivery)",
        "print(json.dumps(delivery))",
    ))
    encoded_code = base64.b64encode(code.encode("utf-8")).decode("ascii")
    shell = ("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
             "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
             "Set-Location 'C:\\CodeWJH\\Projects\\Codexio';"
             f"$Code=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_code}'));"
             "& .\\.venv\\Scripts\\python.exe -X utf8 -c $Code;exit $LASTEXITCODE")
    encoded_shell = base64.b64encode(shell.encode("utf-16le")).decode("ascii")
    for address in WINDOWS_HOSTS:
        response = run("ssh", *WINDOWS_SSH_OPTIONS, "WJH@" + address, "powershell.exe",
                       "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_shell,
                       capture=True, check=False)
        if response.returncode == 0:
            break
        if response.returncode != 255:
            raise UpdateError("Windows 构建主机预检失败：" + (response.stderr or response.stdout).strip())
    else:
        raise UpdateError("Windows 局域网和备用地址均无法连接；未推送或发布，也不会自动使用云端构建")
    delivery = json.loads(response.stdout)
    remote_path = str(delivery.get("executable") or "").replace("\\", "/")
    if (delivery.get("version") != version
            or not re.fullmatch(r"C:/CodeWJH/Projects/Codexio/build/dev/windows/(?:pending/)?Codexio\.exe", remote_path, re.IGNORECASE)):
        raise UpdateError("Windows 产物版本或保留路径不符合交付约定")
    package = directory / "Codexio.exe"
    local = ROOT / "build/dev/windows/Codexio.exe"
    if local.is_file() and file_sha256(local) == delivery["sha256"]:
        shutil.copy2(local, package)
    else:
        run("scp", "-C", *WINDOWS_SSH_OPTIONS, "WJH@" + address + ":" + remote_path, package)
    if file_sha256(package) != delivery["sha256"] or package.stat().st_size != delivery["size"]:
        raise UpdateError("Windows 回传产物与已验证构建不一致")
    manifest_path = directory / "latest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(version=version, url=f"https://github.com/{REPOSITORY}/releases/download/v{version}/Codexio.exe",
                    sha256=delivery["sha256"], size=delivery["size"], notes="Codexio " + version)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verify_release(directory, version, platform="both", include_ios=True)
    return {"host": address, "sha256": delivery["sha256"], "source_sha256": delivery["source_sha256"]}


def validate_repository(version, include_windows=False):
    mac_version = (ROOT / "macos/VERSION").read_text(encoding="utf-8").strip()
    if mac_version != version:
        raise UpdateError("Mac 源码版本 %s 与确认发布版本 %s 不一致" % (mac_version, version))
    if include_windows and (ROOT / "windows/VERSION").read_text(encoding="utf-8").strip() != version:
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
    allowed = release_assets(include_windows)
    if any(name not in allowed for name in names):
        raise UpdateError("草稿 Release 的附件集合不符合当前发布约定")
    downloaded = staging / "draft-macos"
    downloaded.mkdir()
    run("gh", "release", "upload", tag, "--repo", REPOSITORY, "--clobber",
        *(mac_stage / name for name in allowed))
    run("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", downloaded, "--clobber")
    verify_release(downloaded, tag.removeprefix("v"), platform="both" if include_windows else "macos", include_ios=True)
    for name in allowed:
        if file_sha256(downloaded / name) != file_sha256(mac_stage / name):
            raise UpdateError("草稿中的 Mac 附件与本机已验证开发包不一致：" + name)


def create_draft(tag, commit, mac_stage, staging, notes="", include_windows=False):
    if gh_release(tag) is not None or remote_ref("refs/tags/" + tag) is not None:
        raise UpdateError("同版本 Tag 或 Release 已存在，不自动覆盖")
    local_tag = run("git", "show-ref", "--verify", "--quiet", "refs/tags/" + tag, check=False)
    if local_tag.returncode == 0:
        raise UpdateError("本地同版本 Tag 已存在，不自动覆盖")
    notes_path = write_notes(staging, notes)
    run("gh", "release", "create", tag, "--repo", REPOSITORY, "--target", commit,
        "--title", tag, "--draft", "--notes-file", notes_path,
        *(mac_stage / name for name in release_assets(include_windows)))
    release = gh_release(tag)
    if not release or release.get("isDraft") is not True or release.get("targetCommitish") != commit:
        raise UpdateError("未能确认 GitHub 草稿 Release")
    validate_notes(release, notes)
    return release


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


def finalize_draft(tag, commit, staging, mac_stage, notes="", include_windows=False):
    release = gh_release(tag)
    if (not release or release.get("isDraft") is not True or release.get("tagName") != tag
            or release.get("name") != tag or release.get("targetCommitish") != commit):
        raise UpdateError("草稿状态不符合发布条件")
    validate_notes(release, notes)
    assets = release_assets(include_windows)
    if sorted(item.get("name") for item in release.get("assets") or []) != sorted(assets):
        raise UpdateError("草稿附件集合与本次确认的平台不一致")
    downloaded = staging / "verified-mac-draft"
    downloaded.mkdir()
    run("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", downloaded)
    verify_release(downloaded, tag.removeprefix("v"), platform="both" if include_windows else "macos", include_ios=True)
    for name in assets:
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


def publish(version, *, resume_draft=False, sync_only=False, include_windows=True, notes=None):
    version = ".".join(map(str, version_tuple(version)))
    tag = "v" + version
    sections = {}
    if not sync_only:
        notes_path = ROOT / "build/staging/release-notes" / (tag + ".md")
        if notes is None:
            if not notes_path.is_file():
                raise UpdateError("请先准备简短、重要内容在前的 Release 正文：" + str(notes_path))
            notes = notes_path.read_text(encoding="utf-8")
        if not notes.strip():
            raise UpdateError("新 Release 正文不能为空；请先准备本次实际功能与修复的简短说明")
        sections = platform_notes(notes)
    if (ROOT / "release" / version).exists():
        raise UpdateError("正式版本目录已存在，不自动覆盖：" + str(ROOT / "release" / version))
    staging = ROOT / "build/staging/release-coordinator" / uuid.uuid4().hex
    staging.mkdir(parents=True)
    try:
        commit = validate_repository(version, include_windows and not sync_only)
        if sync_only:
            previous = gh_release(tag)
            if notes is None:
                notes = str((previous or {}).get("body") or "")
            has_ios = any(asset.get("name") == IPA_NAME for asset in (previous or {}).get("assets") or [])
            has_windows = any(asset.get("name") == "Codexio.exe" for asset in (previous or {}).get("assets") or [])
            verify_published(tag, commit, has_windows, include_ios=has_ios, notes=notes)
            return sync_local_release(version, tag, staging, has_windows, include_ios=has_ios)
        mac_stage = validate_mac(version, staging)
        prepare_ios(version, mac_stage)
        if include_windows:
            prepare_windows(version, commit, mac_stage)
        else:
            preserve_published_windows(mac_stage, staging)
        validate_ios_notes(sections, staging)
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
            create_draft(tag, commit, mac_stage, staging, notes=notes, include_windows=include_windows)
        finalize_draft(tag, commit, staging, mac_stage, notes=notes, include_windows=include_windows)
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
                        help="只下载并核验已发布的完整附件集合，补齐本地 release 归档")
    parser.add_argument("--include-windows", action="store_true",
                        help="兼容参数；新发布默认已包含在本地 Windows 主机验证过的 EXE")
    parser.add_argument("--apple-only", action="store_true",
                        help="仅在用户明确要求时只发布 Mac/iOS，保留已发布的 Windows 清单字段")
    parser.add_argument("--append-windows", action="store_true",
                        help="从 Windows 构建主机向既有正式 Release 补充已验证 EXE，保留 Tag/正文/Apple 附件")
    parser.add_argument("--asset-only", action="store_true",
                        help="仅追加 EXE，不修改共享清单；用于用户明确保留不同 Windows 版本的情形")
    parser.add_argument("--prepare-only", action="store_true",
                        help="仅准备并验证追加 Windows 附件的候选文件，不推送或上传")
    parser.add_argument("--notes-file", type=Path,
                        help="读取 UTF-8 Release 正文；新发布默认读取 build/staging/release-notes/v<版本>.md，不能为空")
    args = parser.parse_args()
    if not args.confirm_publish and not args.sync_only and not (args.append_windows and args.prepare_only):
        parser.error("发布必须显式提供 --confirm-publish")
    try:
        if args.append_windows:
            if args.resume_draft or args.include_windows or args.apple_only or args.notes_file or args.sync_only and args.prepare_only:
                raise UpdateError("追加 Windows 附件不能同时创建/改写草稿、正文或触发旧 CI")
            from append_windows_release import append_windows
            print(append_windows(args.version, asset_only=args.asset_only, prepare_only=args.prepare_only, sync_only=args.sync_only))
            return
        if args.asset_only or args.prepare_only:
            raise UpdateError("--asset-only / --prepare-only 仅用于 --append-windows")
        if args.include_windows and args.apple_only:
            raise UpdateError("--include-windows 不能与 --apple-only 同时使用")
        notes = args.notes_file.read_text(encoding="utf-8") if args.notes_file else None
        print(publish(args.version, resume_draft=args.resume_draft, sync_only=args.sync_only,
                      include_windows=not args.apple_only, notes=notes))
    except (OSError, ValueError, UpdateError, subprocess.SubprocessError) as exc:
        parser.exit(1, "发布未完成：%s\n" % exc)


if __name__ == "__main__":
    main()
