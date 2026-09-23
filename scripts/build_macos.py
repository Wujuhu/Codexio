"""Build a verified Mac development app and ZIP under build/dev/macos only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
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
WIDGET_VERSION = 3  # Increase when changing the extension's public behavior.


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


def embed_widget(bundle):
    """Embed a stable native WidgetKit extension in the PyInstaller app."""
    sources = ROOT / "macos/widget"
    widget_source = sources / "CodexioWidget.swift"
    bridge_source = sources / "WidgetBridge.swift"
    entitlements = sources / "Widget.entitlements"
    digest = hashlib.sha256()
    for path in (widget_source, bridge_source, entitlements):
        digest.update(path.read_bytes())
    digest.update(("macos15-widget-%s-%s" % (WIDGET_VERSION, platform.machine())).encode("ascii"))
    cache = BUILD / "cache/macos-widget" / digest.hexdigest()[:20]
    extension = cache / "CodexioWidget.appex"
    bridge = cache / "libCodexioWidgetBridge.dylib"
    if not (extension / "Contents/MacOS/CodexioWidget").is_file() or not bridge.is_file():
        if cache.exists():
            shutil.rmtree(cache)
        (extension / "Contents/MacOS").mkdir(parents=True)
        (extension / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "com.wujuhu.codexio.widget",
            "CFBundleExecutable": "CodexioWidget",
            "CFBundleName": "Codexio Widget",
            "CFBundleDisplayName": "Codexio",
            "CFBundlePackageType": "XPC!",
            "CFBundleVersion": str(WIDGET_VERSION),
            "CFBundleShortVersionString": "1.%d" % (WIDGET_VERSION - 1),
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleDevelopmentRegion": "zh_CN",
            "CFBundleSupportedPlatforms": ["MacOSX"],
            "DTPlatformName": "macosx",
            "LSMinimumSystemVersion": "15.0",
            "NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"},
        }))
        environment = dict(os.environ)
        if not environment.get("DEVELOPER_DIR") and Path("/Applications/Xcode.app/Contents/Developer").is_dir():
            environment["DEVELOPER_DIR"] = "/Applications/Xcode.app/Contents/Developer"
        target = platform.machine() + "-apple-macos15.0"
        # WidgetKit extensions enter through Foundation's NSExtensionMain. A
        # regular Swift @main executable registers with PlugInKit but crashes
        # before WidgetKit can enumerate its configurations.
        run("xcrun", "swiftc", "-target", target, "-application-extension", "-parse-as-library",
            "-Xlinker", "-e", "-Xlinker", "_NSExtensionMain", widget_source,
            "-o", extension / "Contents/MacOS/CodexioWidget", env=environment)
        run("codesign", "--force", "--sign", "-", "--timestamp=none", "--entitlements", entitlements, extension)
        run("xcrun", "swiftc", "-target", target, "-emit-library", "-module-name", "CodexioWidgetBridge",
            bridge_source, "-o", bridge, env=environment)
        run("codesign", "--force", "--sign", "-", "--timestamp=none", bridge)
    run("codesign", "--verify", "--strict", extension)
    run("codesign", "--verify", "--strict", bridge)
    destination = bundle / "Contents/PlugIns/CodexioWidget.appex"
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(extension, destination, symlinks=True)
    shutil.copy2(bridge, bundle / "Contents/Frameworks/libCodexioWidgetBridge.dylib")
    # Seal the new nested code without changing the extension's own signature.
    run("codesign", "--force", "--sign", "-", "--timestamp=none", bundle)


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
    embed_widget(bundle)
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
