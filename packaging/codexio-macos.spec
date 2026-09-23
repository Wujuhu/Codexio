# Build on the target Mac; shared resources and application version stay in src.
import platform
import re
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "codexio/__init__.py").read_text(encoding="utf-8")).group(1)

a = Analysis(
    [str(SRC / "codexio/__main__.py")],
    pathex=[str(SRC)],
    datas=[(str(SRC / "codexio/icons"), "codexio/icons"),
           (str(SRC / "codexio/pricing_seed.json"), "codexio"),
           # SSH scanner is sent as UTF-8 source to an explicitly configured host.
           (str(SRC / "codexio/usage_collector.py"), "codexio")],
    hiddenimports=["codexio.macos_smoke"],
    excludes=["pytest", "tkinter", "PySide6.QtTest", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtPdf", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
              "codexio.window", "codexio.dock", "codexio.tray"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="Codexio", debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    # None lets PyInstaller perform ad-hoc signing without enabling hardened
    # runtime library validation, which requires a real Developer ID Team ID.
    argv_emulation=False, target_arch=platform.machine(), codesign_identity=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Codexio")
app = BUNDLE(
    collection, name="Codexio.app", icon=str(ROOT / "build/cache/macos-resources/Codexio.icns"),
    bundle_identifier="com.wujuhu.codexio", version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Codexio",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "15.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSSupportsAutomaticTermination": False,
    },
)
