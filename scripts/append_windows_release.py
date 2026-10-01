"""Append a verified local Windows build to an existing Apple release.

Called by publish_release_from_macos.py --append-windows. No CI, tag, release
body, Mac ZIP or IPA is changed. A version-matched build also updates only the
Windows fields of the shared manifest; --asset-only preserves that manifest.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

from codexio.updates import UpdateError, file_sha256, version_tuple
from verify_release import verify_release

REPOSITORY = "Wujuhu/Codexio"
APPLE = ("Codexio.app.zip", "Codexio.ipa", "latest.json")


def run(*args):
    result = subprocess.run(list(map(str, args)), cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise UpdateError((result.stderr or result.stdout).strip() or "Command failed")
    return result.stdout.strip()


def release(tag):
    return json.loads(run("gh", "release", "view", tag, "--repo", REPOSITORY, "--json",
                         "tagName,name,isDraft,isPrerelease,targetCommitish,body,assets,url"))


def ref(name):
    value = run("git", "ls-remote", "origin", name)
    return value.split()[0] if value else None


def powershell(script):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise UpdateError("Windows PowerShell is required for EXE and source verification")
    return json.loads(run(shell, "-NoProfile", "-NonInteractive", "-Command",
                          "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);" + script))


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def validate_windows(version, package, delivery):
    if not package.is_file() or not package.resolve().is_relative_to(ROOT / "build"):
        raise UpdateError("Windows EXE must be an existing verified development build")
    if delivery.get("version") != version or delivery.get("sha256") != file_sha256(package) or delivery.get("size") != package.stat().st_size:
        raise UpdateError("Windows EXE does not match its delivery record")
    smoke = json.loads(Path(delivery["smoke_result"]).read_text(encoding="utf-8-sig"))
    if not smoke.get("ok") or smoke.get("checks") != ["程序启动", "基本数据显示", "主窗口关闭与重开"]:
        raise UpdateError("The original three-item Windows smoke did not pass")
    with package.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise UpdateError("Invalid Windows executable")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(6) != b"PE\0\0\x64\x86":
            raise UpdateError("Windows package must be x64")
    values = powershell(
        f". {literal(ROOT / 'scripts/windows_build_common.ps1')};"
        f"$f=Get-CodexioWindowsFingerprint -ProjectRoot {literal(ROOT)};"
        f"$v=(Get-Item -LiteralPath {literal(package)}).VersionInfo;"
        "[ordered]@{source=$f.sha256;product=$v.ProductVersion;file=$v.FileVersion;name=$v.ProductName}|ConvertTo-Json")
    if values != {"source": delivery["source_sha256"], "product": version, "file": version, "name": "Codexio"}:
        raise UpdateError("Windows source fingerprint or PE version is stale")


def same_release(before, after):
    for key in ("tagName", "name", "isDraft", "isPrerelease", "targetCommitish", "body"):
        if before.get(key) != after.get(key):
            raise UpdateError("The existing release changed concurrently: " + key)
    for name in APPLE[:2]:
        old = next(x for x in before["assets"] if x["name"] == name)
        new = next((x for x in after["assets"] if x["name"] == name), {})
        if any(old.get(key) != new.get(key) for key in ("id", "size", "digest", "updatedAt")):
            raise UpdateError("Existing Apple asset changed: " + name)


def append_windows(version, *, asset_only=False, prepare_only=False, sync_only=False):
    if os.name != "nt":
        raise UpdateError("Append the locally verified EXE from its Windows build host")
    version = ".".join(map(str, version_tuple(version)))
    windows_version = (ROOT / "windows/VERSION").read_text(encoding="utf-8").strip()
    if not asset_only and windows_version != version:
        raise UpdateError("Confirm a matching Windows version, or explicitly choose --asset-only")
    delivery_path = ROOT / f"build/checks/v{windows_version}/delivery.json"
    delivery = json.loads(delivery_path.read_text(encoding="utf-8-sig"))
    package = Path(delivery["executable"])
    validate_windows(windows_version, package, delivery)
    if run("git", "branch", "--show-current") != "main":
        raise UpdateError("Only main may be pushed by this coordinator")
    if not prepare_only and not sync_only and run("git", "status", "--porcelain"):
        raise UpdateError("Commit the verified merge and release preparation first")
    tag = "v" + version
    before = release(tag)
    expected_before = set(APPLE)
    names = {x["name"] for x in before["assets"]}
    if before.get("isDraft") or before.get("isPrerelease") or before.get("tagName") != tag or before.get("name") != tag or names not in (expected_before, expected_before | {"Codexio.exe"}):
        raise UpdateError("Target must be the existing published Apple release")
    tag_sha = ref("refs/tags/" + tag)
    if not tag_sha:
        raise UpdateError("Existing release tag is missing")
    # The merged checkout must preserve the Apple source behind that release.
    run("git", "diff", "--exit-code", tag, "--", "macos", "ios", "apple/shared", "scripts/build_macos.py", "scripts/build_ios.py", "scripts/ios_release.py", "src/codexio/icons")
    stage = ROOT / "build/staging" / ("append-windows-" + tag + "-" + uuid.uuid4().hex)
    existing, candidate, downloaded = (stage / name for name in ("existing", "candidate", "downloaded"))
    for path in (existing, candidate, downloaded):
        if not path.resolve().is_relative_to(ROOT / "build"):
            raise UpdateError("Release staging resolves outside this workspace's build directory")
        path.mkdir(parents=True)
    run("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", existing,
        "--pattern", APPLE[0], "--pattern", APPLE[1], "--pattern", APPLE[2])
    verify_release(existing, version, platform="macos", include_ios=True)
    original = json.loads((existing / "latest.json").read_text(encoding="utf-8-sig"))
    for name in APPLE:
        asset = next(x for x in before["assets"] if x["name"] == name)
        if asset.get("digest") != "sha256:" + file_sha256(existing / name) or asset["size"] != (existing / name).stat().st_size:
            raise UpdateError("Downloaded release asset does not match GitHub metadata: " + name)
        shutil.copy2(existing / name, candidate / name)
    shutil.copy2(package, candidate / "Codexio.exe")
    if not asset_only and not sync_only:
        manifest = dict(original)
        manifest.update(version=windows_version,
                        url=f"https://github.com/{REPOSITORY}/releases/download/{tag}/Codexio.exe",
                        sha256=delivery["sha256"], size=delivery["size"],
                        notes=original.get("notes", "") if original.get("version") == windows_version and original.get("sha256") == delivery["sha256"] else "Codexio " + windows_version)
        (candidate / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not asset_only:
        verify_release(candidate, version, platform="both", include_ios=True)
    report = {"release": before["url"], "release_version": version, "windows_version": windows_version,
              "sha256": delivery["sha256"], "size": delivery["size"], "asset_only": asset_only,
              "source_sha256": delivery["source_sha256"], "tag_commit": tag_sha, "staging": str(stage)}
    (stage / "before.json").write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
    if prepare_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return stage
    commit = run("git", "rev-parse", "HEAD")
    if not sync_only:
        run("git", "push", "origin", "main")
        if ref("refs/heads/main") != commit or ref("refs/tags/" + tag) != tag_sha:
            raise UpdateError("Remote main/tag verification failed; no release upload attempted")
        current = release(tag)
        same_release(before, current)
        exe = next((x for x in current["assets"] if x["name"] == "Codexio.exe"), None)
        if exe:
            if exe.get("digest") != "sha256:" + delivery["sha256"] or exe["size"] != delivery["size"]:
                raise UpdateError("A different Windows EXE is already published; refusing to replace it")
        else:
            run("gh", "release", "upload", tag, candidate / "Codexio.exe", "--repo", REPOSITORY)
        current = release(tag)
        same_release(before, current)
        uploaded_exe = next((x for x in current["assets"] if x["name"] == "Codexio.exe"), {})
        if uploaded_exe.get("state") != "uploaded" or uploaded_exe.get("digest") != "sha256:" + delivery["sha256"] or uploaded_exe.get("size") != delivery["size"]:
            raise UpdateError("Windows upload metadata did not verify; shared manifest was not changed")
        current_manifest = next(x for x in current["assets"] if x["name"] == "latest.json")
        wanted_manifest = file_sha256(candidate / "latest.json")
        if current_manifest.get("digest") not in ("sha256:" + file_sha256(existing / "latest.json"), "sha256:" + wanted_manifest):
            raise UpdateError("The shared manifest changed concurrently; Windows EXE was retained")
        if not asset_only and current_manifest.get("digest") != "sha256:" + wanted_manifest:
            match = re.fullmatch(r"https://api\.github\.com/repos/Wujuhu/Codexio/releases/assets/([0-9]+)", str(current_manifest.get("apiUrl") or ""))
            if not match:
                raise UpdateError("Missing exact REST asset identity for the verified manifest")
            try:
                # Delete only the immutable asset identity whose bytes we verified.
                # A concurrent replacement cannot be removed through a name lookup.
                run("gh", "api", "--method", "DELETE", "repos/" + REPOSITORY + "/releases/assets/" + match[1])
                run("gh", "release", "upload", tag, candidate / "latest.json", "--repo", REPOSITORY)
            except UpdateError:
                # gh replacement deletes the old asset first. Restore only if absent;
                # never overwrite a concurrent publisher's successfully uploaded file.
                latest = next((x for x in release(tag)["assets"] if x["name"] == "latest.json"), None)
                if latest is None:
                    run("gh", "release", "upload", tag, existing / "latest.json", "--repo", REPOSITORY)
                raise
    published = release(tag)
    same_release(before, published)
    if {x["name"] for x in published["assets"]} != set(APPLE) | {"Codexio.exe"} or ref("refs/tags/" + tag) != tag_sha:
        raise UpdateError("Final release attachment/tag check failed")
    run("gh", "release", "download", tag, "--repo", REPOSITORY, "--dir", downloaded)
    for name in (*APPLE, "Codexio.exe"):
        if file_sha256(downloaded / name) != file_sha256(candidate / name):
            raise UpdateError("Published download differs from the verified candidate: " + name)
    if not asset_only:
        verify_release(downloaded, version, platform="both", include_ios=True)
    destination = ROOT / "release" / version
    if not destination.resolve().is_relative_to(ROOT) or destination.parent.resolve() != ROOT / "release":
        raise UpdateError("Remote succeeded; local release archive resolves outside its intended directory")
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        downloaded.rename(destination)
    else:
        if destination.is_symlink() or {p.name for p in destination.iterdir()} - (set(APPLE) | {"Codexio.exe"}):
            raise UpdateError("Remote succeeded; local archive contains unexpected files and was preserved")
        # Preserve any running EXE. Only absent/identical binaries are accepted;
        # update the manifest atomically after every payload is in place.
        for name in (*APPLE[:2], "Codexio.exe", "latest.json"):
            target = destination / name
            if target.exists() and file_sha256(target) == file_sha256(downloaded / name):
                continue
            if target.exists():
                if name != "latest.json":
                    raise UpdateError("Remote succeeded; a different local archive file was preserved: " + name)
                old = json.loads(target.read_text(encoding="utf-8-sig"))
                new = json.loads((downloaded / name).read_text(encoding="utf-8-sig"))
                windows_fields = {"version", "url", "sha256", "size", "notes"}
                if {k: v for k, v in old.items() if k not in windows_fields} != {k: v for k, v in new.items() if k not in windows_fields}:
                    raise UpdateError("Remote succeeded; local Apple/platform manifest fields differ and were preserved")
            temporary = destination / ("." + name + ".tmp")
            shutil.copy2(downloaded / name, temporary)
            temporary.replace(target)
    run("git", "fetch", "origin", "tag", tag)
    report.update(commit=commit, archive=str(destination), verified=True)
    checks = ROOT / "build/checks" / ("release-" + tag + "-windows.json")
    checks.parent.mkdir(parents=True, exist_ok=True)
    checks.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return destination
