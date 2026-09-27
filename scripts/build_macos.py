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
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
STAGING = BUILD / "staging/macos"
DESTINATION = BUILD / "dev/macos"
WIDGET_VERSION = 19  # Increase for widget UI, registration, or host-lifecycle changes.


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


def swift_environment():
    environment = dict(os.environ)
    xcode = Path("/Applications/Xcode.app/Contents/Developer")
    if not environment.get("DEVELOPER_DIR") and xcode.is_dir():
        environment["DEVELOPER_DIR"] = str(xcode)
    return environment


def localization_resources(destination):
    translations = {}
    shared = ROOT / "src/codexio/translations.json"
    if shared.exists():
        translations.update(json.loads(shared.read_text(encoding="utf-8")))
    pattern = re.compile(r'(?:L|WL)\(("(?:\\.|[^"\\])*"),\s*("(?:\\.|[^"\\])*")\)')
    for source in [*sorted((ROOT / "macos/native").glob("*.swift")), *sorted((ROOT / "macos/widget").glob("*.swift"))]:
        for chinese, english in pattern.findall(source.read_text(encoding="utf-8")):
            translations[json.loads(chinese)] = json.loads(english)
    translations.update({
        "Codexio 请求": "Codexio Request", "查看最近请求、费用与剩余额度": "View recent requests, cost and remaining allowance",
        "打开 Codexio 查看最近请求": "Open Codexio to view recent requests", "耗时": "Duration",
        "Codex 额度": "Codex Allowance", "额度样式": "Allowance style", "额度窗口": "Allowance window",
        "单额度": "Single limit", "双额度": "Dual limits", "双额度刻度条": "Segmented dual limits",
        "选择额度样式、窗口和外观": "Choose the style, window and appearance", "样式": "Style",
        "单额度窗口": "Single-limit window", "外观": "Appearance", "跟随系统": "Follow system",
        "浅色": "Light", "深色": "Dark", "周额度": "Weekly limit", "5 小时额度": "5-hour limit",
        "仅显示额度，提供三种样式": "Allowance only, with three styles",
        "Codex 单额度": "Codex Weekly Allowance", "Codex 双额度": "Codex Dual Allowance",
        "Codex 刻度额度": "Codex Segmented Allowance", "查看 Codex 剩余额度与重置时间": "View Codex remaining allowance and reset times",
    })
    strings = {key: {"localizations": {"en": {"stringUnit": {"state": "translated", "value": value}},
                                      "zh-Hans": {"stringUnit": {"state": "translated", "value": key}}}}
               for key, value in sorted(translations.items())}
    catalog = ROOT / "macos/Resources/Localizable.xcstrings"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({"sourceLanguage": "en", "strings": strings, "version": "1.0"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for language in ("en", "zh-Hans"):
        folder = destination / (language + ".lproj")
        folder.mkdir(parents=True, exist_ok=True)
        values = {key: value if language == "en" else key for key, value in translations.items()}
        (folder / "Localizable.strings").write_bytes(plistlib.dumps(values, fmt=plistlib.FMT_BINARY))


def build_native(bundle, version):
    if bundle.exists():
        shutil.rmtree(bundle)
    executable = bundle / "Contents/MacOS/Codexio"
    resources = bundle / "Contents/Resources"
    executable.parent.mkdir(parents=True)
    resources.mkdir(parents=True)
    environment = swift_environment()
    target = platform.machine() + "-apple-macos15.0"
    sources = sorted((ROOT / "macos/native").glob("*.swift")) + sorted((ROOT / "macos/widget").glob("*.swift"))
    cache = BUILD / "cache/swift"
    cache.mkdir(parents=True, exist_ok=True)
    run("xcrun", "swiftc", "-swift-version", "5", "-O", "-whole-module-optimization", "-target", target,
        "-parse-as-library", "-D", "CODEXIO_APP_WIDGET_PREVIEW", "-module-name", "Codexio", "-module-cache-path", cache,
        *sources, "-o", executable, env=environment)
    info = {
        "CFBundleIdentifier": "com.wujuhu.codexio", "CFBundleExecutable": "Codexio",
        "CFBundleName": "Codexio", "CFBundleDisplayName": "Codexio", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version, "CFBundleVersion": version,
        "CodexioWidgetBuild": str(WIDGET_VERSION),
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleDevelopmentRegion": "en",
        "CFBundleLocalizations": ["en", "zh-Hans"], "CFBundleIconFile": "Codexio.icns",
        "LSMinimumSystemVersion": "15.0", "NSHighResolutionCapable": True,
        "NSPrincipalClass": "NSApplication", "NSSupportsAutomaticTermination": False,
        "CFBundleSupportedPlatforms": ["MacOSX"],
        "CFBundleURLTypes": [{"CFBundleURLName": "Codexio", "CFBundleURLSchemes": ["codexio"]}],
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True, "NSAllowsLocalNetworking": True},
    }
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    for name in ("app-light.svg", "app-dark.svg", "app-light.png", "app-dark.png", "brand-mark.svg", "brand-mark.png"):
        shutil.copy2(ROOT / "src/codexio/icons" / name, resources / name)
    for source in (ROOT / "src/codexio/icons/task-status").glob("*"):
        shutil.copy2(source, resources / source.name)
    iconset = BUILD / "cache/macos-resources/Codexio.iconset"
    run(executable, "--render-icon", iconset)
    run("iconutil", "-c", "icns", iconset, "-o", resources / "Codexio.icns")
    shutil.copy2(ROOT / "src/codexio/pricing_seed.json", resources / "pricing_seed.json")
    localization_resources(resources)


def embed_widget(bundle):
    sources = sorted((ROOT / "macos/widget").glob("*.swift"))
    entitlements = ROOT / "macos/widget/Widget.entitlements"
    environment = swift_environment()
    digest = hashlib.sha256(b"".join(path.read_bytes() for path in [*sources, entitlements]))
    digest.update(("native-widget-%s-%s" % (WIDGET_VERSION, platform.machine())).encode("ascii"))
    cache = BUILD / "cache/macos-widget" / digest.hexdigest()[:20]
    extension = bundle / "Contents/PlugIns/CodexioWidget.appex"
    executable = extension / "Contents/MacOS/CodexioWidget"
    executable.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    target = platform.machine() + "-apple-macos15.0"
    run("xcrun", "swiftc", "-swift-version", "5", "-O", "-whole-module-optimization", "-target", target,
        "-application-extension", "-parse-as-library", "-module-name", "CodexioWidget",
        "-Xlinker", "-e", "-Xlinker", "_NSExtensionMain", *sources, "-o", executable, env=environment)
    resources = extension / "Contents/Resources"
    resources.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "src/codexio/icons/brand-mark.png", resources / "brand-mark.png")
    localization_resources(resources)
    (extension / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "com.wujuhu.codexio.widget", "CFBundleExecutable": "CodexioWidget",
        "CFBundleName": "Codexio Widget", "CFBundleDisplayName": "Codexio", "CFBundlePackageType": "XPC!",
        "CFBundleVersion": str(WIDGET_VERSION), "CFBundleShortVersionString": "1.%d" % (WIDGET_VERSION - 1),
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleDevelopmentRegion": "en", "CFBundleLocalizations": ["en", "zh-Hans"],
        "CFBundleSupportedPlatforms": ["MacOSX"], "DTPlatformName": "macosx", "LSMinimumSystemVersion": "15.0",
        "NSExtension": {"NSExtensionPointIdentifier": "com.apple.widgetkit-extension"},
    }))
    run("codesign", "--force", "--sign", "-", "--timestamp=none", "--entitlements", entitlements, extension)
    run("codesign", "--force", "--sign", "-", "--timestamp=none", bundle)
    run("codesign", "--verify", "--strict", extension)


def prepare_mock_bundle(bundle, destination):
    """Run the same executable without exposing a second WidgetKit host to macOS."""
    refuse_running(destination)
    if destination.exists():
        shutil.rmtree(destination)
    contents = destination / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    shutil.copy2(bundle / "Contents/MacOS/Codexio", contents / "MacOS/Codexio")
    shutil.copytree(bundle / "Contents/Resources", contents / "Resources")
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    info["CFBundleIdentifier"] = "com.wujuhu.codexio.mock"
    info.pop("CFBundleURLTypes", None)
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    run("codesign", "--force", "--sign", "-", "--timestamp=none", destination)
    return contents / "MacOS/Codexio"


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
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/codexio/__init__.py").read_text(encoding="utf-8")).group(1)
    build_native(bundle, version)
    embed_widget(bundle)
    with (bundle / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/codexio/__init__.py").read_text(encoding="utf-8")).group(1)
    assert info["CFBundleShortVersionString"] == info["CFBundleVersion"] == version
    widget_info = plistlib.loads((bundle / "Contents/PlugIns/CodexioWidget.appex/Contents/Info.plist").read_bytes())
    assert widget_info["CFBundleVersion"] == str(WIDGET_VERSION)
    assert widget_info["CFBundleShortVersionString"] == "1.%d" % (WIDGET_VERSION - 1)
    assert not any(path.name.startswith(("Python", "Qt", "PySide")) for path in bundle.rglob("*")), "Mac 包不能包含 Python 或 Qt 运行时"
    run("lipo", "-verify_arch", platform.machine(), bundle / "Contents/MacOS/Codexio")
    run("codesign", "--verify", "--deep", "--strict", bundle)
    smoke = BUILD / "checks/macos-smoke"
    (smoke / "result.json").unlink(missing_ok=True)
    # Validate the new binary in a separate data directory, without the shell's
    # import paths or development interpreter influencing the bundled runtime.
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME", "QT_QPA_PLATFORM", "QT_PLUGIN_PATH")}
    mock_executable = prepare_mock_bundle(bundle, BUILD / "checks/mock-host/CodexioMock.app")
    run(mock_executable, "--mock", "--smoke-test", smoke, env=env, timeout=100)
    assert json.loads((smoke / "result.json").read_text(encoding="utf-8"))["ok"]
    from codexio.app_archive import APP_ARCHIVE_NAME
    from codexio.macos_updater import MANIFEST_NAME, _prepare_bundle
    archive = staging / APP_ARCHIVE_NAME
    archive.unlink(missing_ok=True)
    run("ditto", "-c", "-k", "--keepParent", "--norsrc", "--noextattr", bundle, archive)
    # Exercise the same extraction, signature and architecture checks as the updater.
    archive_check = BUILD / "checks/macos-archive"
    refuse_running(archive_check / "Codexio.pending")
    if archive_check.exists():
        shutil.rmtree(archive_check)
    archive_check.mkdir(parents=True)
    shutil.copy2(archive, archive_check / "package.bin")
    try:
        _prepare_bundle(archive_check, archive_check / "Codexio.pending", version)
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
    legacy_backup = BUILD / "backups/macos/Codexio.app"
    if legacy_backup.exists():
        refuse_running(legacy_backup)
        subprocess.run(["pluginkit", "-r", str(legacy_backup / "Contents/PlugIns/CodexioWidget.appex")],
                       capture_output=True, check=False)
        shutil.rmtree(legacy_backup)
    # A backup ending in .app is discovered as another WidgetKit host.
    previous = BUILD / "backups/macos/Codexio.app.previous"
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
    # The installed APP owns the desktop widget. Keep this development copy
    # from competing for the same WidgetKit extension identifier.
    for attempt in range(3):
        subprocess.run(["pluginkit", "-r", str(target / "Contents/PlugIns/CodexioWidget.appex")],
                       capture_output=True, check=False)
        if attempt < 2:
            time.sleep(0.5)
    collection = staging / "Codexio"
    if collection.exists():
        shutil.rmtree(collection)
    print(f"\n开发包已验证，Codexio {version}: {DESTINATION}")
    print("正式发布仅在第二次确认后执行 scripts/publish_release_from_macos.py --version <版本号> --confirm-publish。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
