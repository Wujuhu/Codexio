# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import re
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
VERSION = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$',
                    (SRC / "aiquota" / "__init__.py").read_text(encoding="utf-8"), re.MULTILINE).group(1)
VERSION_PARTS = (*map(int, VERSION.split(".")), 0)
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=VERSION_PARTS, prodvers=VERSION_PARTS, mask=0x3F,
                      flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("FileDescription", "AIQuota"), StringStruct("InternalName", "AIQuota"),
        StringStruct("OriginalFilename", "AIQuota.exe"), StringStruct("ProductName", "AIQuota"),
        StringStruct("FileVersion", VERSION), StringStruct("ProductVersion", VERSION),
    ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])],
)

ICON = SRC / "aiquota" / "icons" / "app.ico"
datas = [
    (
        str(SRC / "aiquota" / "fonts" / "AnthropicSansWebText-Regular.ttf"),
        "aiquota/fonts",
    ),
    (str(ICON), "aiquota/icons"),
    (str(SRC / "aiquota" / "icons" / "app.png"), "aiquota/icons"),
    (str(SRC / "aiquota" / "pricing_seed.json"), "aiquota"),
    (str(SRC / "aiquota" / "usage_collector.py"), "aiquota"),
]
hiddenimports = [
    "aiquota",
    "aiquota.__main__",
    "aiquota.app_icon",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shiboken6",
]
excludes = [
    "pytest",
    "tkinter",
    "unittest",
    "pydoc",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.scripts",
]

# Leftover Qt extras that hooks may still copy. Keep widgets + window plugins.
_DROP_PARTS = (
    "webengine",
    "webview",
    "/qml/",
    "\\qml\\",
    "qt6qml",
    "qt6quick",
    "qtqml",
    "qtquick",
    "qt63d",
    "qt3d",
    "quick3d",
    "multimedia",
    "spatialaudio",
    "qt6pdf",
    "designer",
    "charts",
    "graphs",
    "datavisualization",
    "location",
    "positioning",
    "sensors",
    "bluetooth",
    "nfc",
    "serialbus",
    "serialport",
    "remoteobjects",
    "scxml",
    "statemachine",
    "texttospeech",
    "virtualkeyboard",
    "httpserver",
    "websockets",
    "webchannel",
    "qt6help",
    "qt6sql",
    "qt6test",
    "exampleicons",
    "opengl32sw",
    "avcodec",
    "avformat",
    "avutil",
    "swscale",
    "swresample",
    "translations",
    "metatypes",
    "assistant.exe",
    "linguist.exe",
    "qmlls",
    "qmlformat",
    "qmltyperegistrar",
)


def _keep_bundle_item(dest_name: str) -> bool:
    name = dest_name.replace("\\", "/").lower()
    return not any(part in name for part in _DROP_PARTS)


a = Analysis(
    [str(SRC / "aiquota" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
a.binaries = [item for item in a.binaries if _keep_bundle_item(item[0])]
a.datas = [item for item in a.datas if _keep_bundle_item(item[0])]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AIQuota",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON),
    version=VERSION_INFO,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
