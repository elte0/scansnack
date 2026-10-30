# PyInstaller spec for ScanSnack – build: uv run python tools/build_app.py
#   macOS:   dist/ScanSnack.app (architecture of the running Python: arm64 or x86_64)
#   Windows: dist/ScanSnack/ScanSnack.exe
#   Linux:   dist/ScanSnack/ScanSnack
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent
# Explicit UTF-8: on Windows the default encoding is cp1252, and pyproject.toml contains non-ASCII characters
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]

# Unused Qt modules: saves more than half of the size
QT_EXCLUDES = [f"PySide6.{m}" for m in (
    "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender", "QtBluetooth",
    "QtCharts", "QtConcurrent", "QtDataVisualization", "QtDesigner", "QtGraphs", "QtHelp", "QtHttpServer",
    "QtLocation", "QtMultimedia", "QtMultimediaWidgets", "QtNetworkAuth", "QtNfc", "QtOpenGL",
    "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets", "QtPositioning", "QtQml", "QtQuick", "QtQuick3D",
    "QtQuickControls2", "QtQuickWidgets", "QtRemoteObjects", "QtScxml", "QtSensors", "QtSerialBus",
    "QtSerialPort", "QtSpatialAudio", "QtSql", "QtStateMachine", "QtSvg", "QtSvgWidgets", "QtTest",
    "QtTextToSpeech", "QtUiTools", "QtWebChannel", "QtWebEngineCore", "QtWebEngineQuick",
    "QtWebEngineWidgets", "QtWebSockets", "QtWebView", "QtXml",
)]

a = Analysis(
    [str(ROOT / "packaging" / "launch_app.py")],
    pathex=[str(ROOT / "src")],
    # profile_template.json (for claim); package metadata: version number for the update check
    datas=collect_data_files("scansnap") + copy_metadata("scansnap-bridge"),
    excludes=QT_EXCLUDES + ["tkinter", "zeroconf", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScanSnack",
    console=False,
    argv_emulation=False,
    icon=str(ROOT / "packaging" / "ScanSnack.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ScanSnack")

# Windows and Linux: the folder dist/ScanSnack is the result; only macOS gets an .app bundle
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="ScanSnack.app",
        icon=str(ROOT / "packaging" / "ScanSnack.icns"),
        bundle_identifier="com.elte0.scansnack",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "ScanSnack",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSPrincipalClass": "NSApplication",           # Retina rendering
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "13.0",
            # Required since macOS 15 for access to the local network (scanner via UDP/TCP)
            "NSLocalNetworkUsageDescription": "ScanSnack connects to your scanner over the local network.",
            "LSApplicationCategoryType": "public.app-category.productivity",
        },
    )
