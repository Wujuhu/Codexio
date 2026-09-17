"""Stage and verify a Mac application, then deliver its DMG under release/<version>/."""
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
STAGING = BUILD / "release-staging/macos"
DESTINATION = BUILD / "macos"


def run(*args, **kwargs):
    print(" ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, cwd=ROOT, **kwargs)


def refuse_running(bundle):
    result = subprocess.run(["ps", "-axo", "comm="], check=True, capture_output=True, text=True, encoding="utf-8")
    prefix = str(bundle.resolve()) + "/"
    if any(line.strip().startswith(prefix) for line in result.stdout.splitlines()):
        raise RuntimeError(f"{bundle} 正在运行。原文件与暂存版本均已保留；请退出 Codexio 后重新构建。")


def build_icon():
    # Each ICNS representation is rendered directly from SVG, including the
    # 1024-pixel Retina image. Never enlarge a pre-rendered PNG.
    from codexio.app_icon import render_app_image
    resources = BUILD / "macos-resources"
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
    parser = argparse.ArgumentParser(description="本地构建并验证 Codexio.app；可选生成安装镜像。")
    parser.add_argument("--dmg", action="store_true", help="同时生成 release/<版本号>/Codexio.dmg 和共用的 latest.json")
    parser.add_argument("--staging-subdir", help="在构建暂存区使用独立子目录，保留正在运行的旧暂存应用")
    parser.add_argument("--manifest", type=Path, help="待合并的现有 latest.json；默认查找本地或已发布的清单")
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
        "--workpath", BUILD / "pyinstaller-macos", ROOT / "packaging/codexio-macos.spec")
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/codexio/__init__.py").read_text(encoding="utf-8")).group(1)
    assert info["CFBundleShortVersionString"] == info["CFBundleVersion"] == version
    run("codesign", "--verify", "--deep", "--strict", bundle)
    smoke = BUILD / "macos-bundle-check"
    (smoke / "result.json").unlink(missing_ok=True)
    # Validate the new binary in a separate data directory, without the shell's
    # import paths or development interpreter influencing the bundled runtime.
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME", "QT_QPA_PLATFORM", "QT_PLUGIN_PATH")}
    run(bundle / "Contents/MacOS/Codexio", "--mock", "--smoke-test", smoke, env=env, timeout=100)
    assert json.loads((smoke / "result.json").read_text(encoding="utf-8"))["ok"]
    if args.dmg:
        from codexio.macos_updater import DMG_NAME, MANIFEST_NAME
        disk = BUILD / "macos-disk"
        refuse_running(disk / "Codexio.app")
        if disk.exists():
            shutil.rmtree(disk)
        disk.mkdir(parents=True)
        run("ditto", bundle, disk / "Codexio.app")
        (disk / "Applications").symlink_to("/Applications")
        image = staging / DMG_NAME
        run("hdiutil", "create", "-volname", "Codexio", "-srcfolder", disk, "-ov", "-format", "UDZO", image)
        run("hdiutil", "verify", image)
        manifest_args = ["--base", args.manifest] if args.manifest else []
        run(sys.executable, ROOT / "scripts/update_manifest.py", "--platform", "macos",
            "--asset", image, "--version", version, "--output", staging / MANIFEST_NAME, *manifest_args)
        (staging / "latest-macos.json").unlink(missing_ok=True)
    refuse_running(target)
    DESTINATION.mkdir(parents=True, exist_ok=True)
    previous = BUILD / "macos-previous/Codexio.app"
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
    if args.dmg:
        release = ROOT / "release" / version
        release.mkdir(parents=True, exist_ok=True)
        for name in (DMG_NAME, MANIFEST_NAME):
            (staging / name).replace(release / name)
        print(f"交付目录：{release}")
    print(f"\n已验证并打包 Codexio {version}: {target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
