"""Stage, verify, then publish a local Mac application without touching dist/."""
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
    parser.add_argument("--dmg", action="store_true", help="同时生成 build/macos/Codexio.dmg")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("只能在 macOS 上构建 .app")
    bundle = STAGING / "Codexio.app"
    target = DESTINATION / "Codexio.app"
    refuse_running(bundle)
    build_icon()
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", STAGING,
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
        disk = BUILD / "macos-disk"
        if disk.exists():
            shutil.rmtree(disk)
        disk.mkdir(parents=True)
        run("ditto", target, disk / "Codexio.app")
        (disk / "Applications").symlink_to("/Applications")
        image = STAGING / "Codexio.dmg"
        run("hdiutil", "create", "-volname", "Codexio", "-srcfolder", disk, "-ov", "-format", "UDZO", image)
        run("hdiutil", "verify", image)
        image.replace(DESTINATION / "Codexio.dmg")
    else:
        # Keep an old installer out of the current delivery folder.
        old_disk = DESTINATION / "Codexio.dmg"
        if old_disk.exists():
            previous.parent.mkdir(parents=True, exist_ok=True)
            old_disk.replace(previous.parent / "Codexio.dmg")
    print(f"\n已验证并打包 Codexio {version}: {target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
