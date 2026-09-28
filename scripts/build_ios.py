"""Build a local, unsigned, SideStore-resignable native iOS app. No publishing."""
from pathlib import Path
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import zipfile
from ios_release import source_digest, validate_ipa

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
STAGE = BUILD / "staging/ios"
APP = STAGE / "Payload/Codexio.app"
ENV = dict(os.environ, DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer")

def run(*args):
    subprocess.run([str(arg) for arg in args], env=ENV, check=True)

def main():
    version = (ROOT / "ios/VERSION").read_text(encoding="utf-8").strip()
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir(parents=True)
    cache = BUILD / "cache/ios/swift"
    cache.mkdir(parents=True, exist_ok=True)
    sdk = subprocess.check_output(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], env=ENV, text=True, encoding="utf-8").strip()
    sources = sorted((ROOT / "apple/shared").glob("*.swift")) + sorted((ROOT / "ios/App").glob("*.swift"))
    run("xcrun", "--sdk", "iphoneos", "swiftc", "-swift-version", "5", "-O", "-whole-module-optimization", "-parse-as-library", "-sdk", sdk,
        "-target", "arm64-apple-ios26.0", "-module-name", "CodexioIOS", "-module-cache-path", cache,
        "-Xlinker", "-rpath", "-Xlinker", "@executable_path/Frameworks", *sources, "-o", APP / "Codexio")
    shutil.copy2(ROOT / "src/codexio/icons/wordmark.png", APP / "wordmark.png")
    assets = STAGE / "Assets.xcassets/AppIcon.appiconset"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "src/codexio/icons/app-light.png", assets / "AppIcon.png")
    wordmark = assets.parent / "CodexioWordmark.imageset"
    wordmark.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "src/codexio/icons/wordmark.png", wordmark / "wordmark.png")
    (wordmark / "Contents.json").write_text(json.dumps({"images":[{"filename":"wordmark.png","idiom":"universal"}],"info":{"author":"xcode","version":1},"properties":{"template-rendering-intent":"template"}}), encoding="utf-8")
    (assets / "Contents.json").write_text(json.dumps({"images":[{"filename":"AppIcon.png","idiom":"universal","platform":"ios","size":"1024x1024"}],"info":{"author":"xcode","version":1}}), encoding="utf-8")
    run("xcrun", "actool", assets.parent, "--compile", APP, "--platform", "iphoneos", "--minimum-deployment-target", "26.0", "--app-icon", "AppIcon", "--target-device", "iphone", "--output-partial-info-plist", STAGE / "asset-info.plist")
    info = plistlib.loads((STAGE / "asset-info.plist").read_bytes())
    info.update({"CFBundleIdentifier":"com.wujuhu.codexio.ios", "CFBundleExecutable":"Codexio", "CFBundleName":"Codexio", "CFBundleDisplayName":"Codexio",
        "CFBundlePackageType":"APPL", "CFBundleShortVersionString":version, "CFBundleVersion":version, "CFBundleInfoDictionaryVersion":"6.0",
        "CFBundleSupportedPlatforms":["iPhoneOS"], "MinimumOSVersion":"26.0", "UIDeviceFamily":[1], "LSRequiresIPhoneOS":True,
        "UILaunchScreen":{}, "UIApplicationSceneManifest":{"UIApplicationSupportsMultipleScenes":False},
        "NSCameraUsageDescription":"扫描 Mac 上的 Codexio 二维码以配对设备。",
        "NSLocalNetworkUsageDescription":"发现并安全连接你配对的 Mac，优先通过局域网同步必要摘要。", "NSBonjourServices":["_codexio._tcp"],
        "UISupportedInterfaceOrientations":["UIInterfaceOrientationPortrait"], "ITSAppUsesNonExemptEncryption":False})
    (APP / "Info.plist").write_bytes(plistlib.dumps(info))
    # The device binary's linker ad-hoc signature is not a distribution signature.
    run("codesign", "--remove-signature", APP / "Codexio")
    ipa = STAGE / "Codexio.ipa"
    with zipfile.ZipFile(ipa,"w",zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(APP.rglob("*")):
            if path.is_file(): archive.write(path,path.relative_to(STAGE))
    with zipfile.ZipFile(ipa) as archive:
        assert archive.testzip() is None
        assert plistlib.loads(archive.read("Payload/Codexio.app/Info.plist"))["CFBundleShortVersionString"] == version
    run("lipo", "-verify_arch", "arm64", APP / "Codexio")
    validate_ipa(ipa, version)
    output = BUILD / "dev/ios"
    output.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ipa,output / ipa.name)
    (output / "build-info.json").write_text(json.dumps({"version":version,"signed":False,"minimum_ios":"26.0","sha256":hashlib.sha256(ipa.read_bytes()).hexdigest(),"size":ipa.stat().st_size,"source_sha256":source_digest(ROOT),"device_tested":False},indent=2)+"\n",encoding="utf-8")
    print("未签名开发 IPA：",output / ipa.name)

if __name__ == "__main__": main()
