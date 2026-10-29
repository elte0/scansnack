# PyInstaller-Spec für ScanSnack.app – Build: packaging/build_mac.sh
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).parent
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

# Nicht benutzte Qt-Module: spart mehr als die Hälfte der Größe
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
    datas=collect_data_files("scansnap"),          # profile_template.json (für claim)
    excludes=QT_EXCLUDES + ["tkinter", "zeroconf", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ScanSnack", console=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ScanSnack")
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
        "NSPrincipalClass": "NSApplication",           # Retina-Darstellung
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        # Seit macOS 15 Pflicht für Zugriffe ins lokale Netz (Scanner per UDP/TCP)
        "NSLocalNetworkUsageDescription": "ScanSnack connects to your scanner over the local network.",
        "LSApplicationCategoryType": "public.app-category.productivity",
    },
)
