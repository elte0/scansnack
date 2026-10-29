#!/bin/sh
# Baut dist/ScanSnap.app und dist/ScanSnap-<version>.dmg.
#   packaging/build_mac.sh            # App + DMG
#   packaging/build_mac.sh --no-dmg   # nur App
# Signatur: ad hoc. Für Weitergabe ohne Gatekeeper-Warnung: CODESIGN_IDENTITY="Developer ID Application: …"
# setzen und danach notarisieren (xcrun notarytool).
set -eu
cd "$(dirname "$0")/.."

[ -f packaging/ScanSnap.icns ] || uv run python tools/make_icon.py
uv run --extra app pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/ScanSnap.spec

APP=dist/ScanSnap.app
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    codesign --force --deep --options runtime --timestamp --sign "$CODESIGN_IDENTITY" "$APP"
fi
codesign --verify --deep --strict "$APP"
"$APP/Contents/MacOS/ScanSnap" --selftest
echo "App: $APP ($(du -sh "$APP" | cut -f1))"

[ "${1:-}" = "--no-dmg" ] && exit 0
VERSION=$(uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
DMG="dist/ScanSnap-$VERSION.dmg"
STAGE=$(mktemp -d)
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Programme"
rm -f "$DMG"
hdiutil create -volname "ScanSnap" -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG" >/dev/null
rm -rf "$STAGE"
echo "DMG: $DMG ($(du -sh "$DMG" | cut -f1))"
