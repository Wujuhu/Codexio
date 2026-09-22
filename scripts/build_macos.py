"""Build a verified Mac development app and ZIP under build/dev/macos only."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
STAGING = BUILD / "staging/macos"
DESTINATION = BUILD / "dev/macos"


def run(*args, **kwargs):
    print(" ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, cwd=ROOT, **kwargs)


def bundle_running(bundle):
    result = subprocess.run(["ps", "-axo", "comm="], check=True, capture_output=True, text=True, encoding="utf-8")
    prefix = str(bundle.resolve()) + "/"
    return any(line.strip().startswith(prefix) for line in result.stdout.splitlines())


def refuse_running(bundle):
    if bundle_running(bundle):
        raise RuntimeError(f"{bundle} 正在运行。原文件与暂存版本均已保留；请退出 Codexio 后重新构建。")


def build_icon():
    # Each ICNS representation is rendered directly from SVG, including the
    # 1024-pixel Retina image. Never enlarge a pre-rendered PNG.
    from codexio.app_icon import render_app_image
    resources = BUILD / "cache/macos-resources"
    iconset = resources / "Codexio.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            target = iconset / f"icon_{size}x{size}{suffix}.png"
            if not render_app_image(size * scale).save(str(target), "PNG"):
                raise RuntimeError(f"无法生成图标：{target}")
    run("iconutil", "-c", "icns", iconset, "-o", resources / "Codexio.icns")


def main():
    parser = argparse.ArgumentParser(description="构建开发版 APP、APP ZIP 和清单，仅输出到 build/dev/macos。")
    parser.add_argument("--staging-subdir", help="在构建暂存区使用独立子目录，保留正在运行的旧暂存应用")
    parser.add_argument("--manifest", type=Path, help="待合并的现有 latest.json；默认查找本地开发包或正式版清单")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("只能在 macOS 上构建 .app")
    if args.staging_subdir and not re.fullmatch(r"[A-Za-z0-9_-]+", args.staging_subdir):
        parser.error("暂存子目录只能包含字母、数字、下划线和连字符")
    staging = STAGING / args.staging_subdir if args.staging_subdir else STAGING
    bundle = staging / "Codexio.app"
    target = DESTINATION / "Codexio.app"
    refuse_running(bundle)
    build_icon()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", staging,
        "--workpath", BUILD / "cache/pyinstaller/macos", ROOT / "packaging/codexio-macos.spec")
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/codexio/__init__.py").read_text(encoding="utf-8")).group(1)
    assert info["CFBundleShortVersionString"] == info["CFBundleVersion"] == version
    run("codesign", "--verify", "--deep", "--strict", bundle)
    smoke = BUILD / "checks/macos-smoke"
    (smoke / "result.json").unlink(missing_ok=True)
    # Validate the new binary in a separate data directory, without the shell's
    # import paths or development interpreter influencing the bundled runtime.
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME", "QT_QPA_PLATFORM", "QT_PLUGIN_PATH")}
    run(bundle / "Contents/MacOS/Codexio", "--mock", "--smoke-test", smoke, env=env, timeout=100)
    assert json.loads((smoke / "result.json").read_text(encoding="utf-8"))["ok"]
    from codexio.app_archive import APP_ARCHIVE_NAME
    from codexio.macos_updater import MANIFEST_NAME, _prepare_bundle
    archive = staging / APP_ARCHIVE_NAME
    archive.unlink(missing_ok=True)
    run("ditto", "-c", "-k", "--keepParent", "--norsrc", "--noextattr", bundle, archive)
    # Exercise the same extraction, signature and architecture checks as the updater.
    archive_check = BUILD / "checks/macos-archive"
    refuse_running(archive_check / "Codexio.app")
    if archive_check.exists():
        shutil.rmtree(archive_check)
    archive_check.mkdir(parents=True)
    shutil.copy2(archive, archive_check / "package.bin")
    try:
        _prepare_bundle(archive_check, archive_check / "Codexio.app", version)
    finally:
        shutil.rmtree(archive_check)
    manifest_args = ["--base", args.manifest] if args.manifest else []
    run(sys.executable, ROOT / "scripts/update_manifest.py", "--platform", "macos",
        "--asset", archive, "--version", version, "--output", staging / MANIFEST_NAME, *manifest_args)
    if bundle_running(target):
        collection = staging / "Codexio"
        if collection.exists():
            shutil.rmtree(collection)
        print(f"\n开发包已验证，Codexio {version}: {staging}")
        print(f"{target} 正在运行，保留原文件；新版 APP、ZIP 和清单已留在暂存区。")
        return 0
    refuse_running(target)
    DESTINATION.mkdir(parents=True, exist_ok=True)
    previous = BUILD / "backups/macos/Codexio.app"
    refuse_running(previous)
    if previous.exists():
        shutil.rmtree(previous)
    if target.exists():
        previous.parent.mkdir(parents=True, exist_ok=True)
        target.rename(previous)
    try:
        bundle.rename(target)
    except OSError:
        if previous.exists() and not target.exists():
            previous.rename(target)
        raise
    run("codesign", "--verify", "--deep", "--strict", target)
    for name in (APP_ARCHIVE_NAME, MANIFEST_NAME):
        (staging / name).replace(DESTINATION / name)
    collection = staging / "Codexio"
    if collection.exists():
        shutil.rmtree(collection)
    print(f"\n开发包已验证，Codexio {version}: {DESTINATION}")
    print("正式归档仅在确认发布版本后执行 scripts/prepare_release.py --version <版本号>。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
