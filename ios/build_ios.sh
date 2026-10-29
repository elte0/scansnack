#!/bin/zsh
# iOS-App bauen, signieren und per USB aufs iPhone spielen.
#   ./build_ios.sh            erstes gekoppeltes iPhone
#   ./build_ios.sh <Gerät>    Name oder Kennung aus `xcrun devicectl list devices`
# Voraussetzungen: Xcode mit Apple-ID (Team unten), iPhone per USB gekoppelt, Entwicklermodus an.
set -euo pipefail
cd "${0:A:h}"

TEAM=${TEAM:-2BCANYGFHU}
BUNDLE=com.elte0.scansnack
PROJECT=build/scansnack/ios/xcode

# Briefcase braucht ein „echtes“ Python (uv-Python lässt sich nicht als venv kopieren)
if [[ ! -x .venv/bin/briefcase ]]; then
  /opt/homebrew/bin/python3.14 -m venv .venv
  .venv/bin/pip install -q briefcase
fi

# Icons/Startbild aus dem Mac-Icon
ICON=../packaging/ScanSnack.png
mkdir -p resources
for s in 20 29 40 58 60 76 80 87 120 152 167 180 640 1024; do
  [[ resources/icon-$s.png -nt $ICON ]] || sips -z $s $s $ICON --out resources/icon-$s.png >/dev/null
done
for s in 1280 1920; do        # Startbildschirm: Icon mittig auf Weiß
  [[ resources/icon-$s.png -nt $ICON ]] || sips -p $s $s --padColor FFFFFF $ICON --out resources/icon-$s.png >/dev/null
done

if [[ -d $PROJECT ]]; then
  .venv/bin/briefcase update iOS -r --update-resources --no-input
else
  .venv/bin/briefcase create iOS --no-input
fi

xcodebuild -project $PROJECT/ScanSnack.xcodeproj -scheme ScanSnack -configuration Debug \
  -destination 'generic/platform=iOS' -derivedDataPath build/derived \
  -allowProvisioningUpdates DEVELOPMENT_TEAM=$TEAM CODE_SIGN_STYLE=Automatic \
  -quiet build
APP=build/derived/Build/Products/Debug-iphoneos/ScanSnack.app
codesign --verify --strict $APP

if [[ -z ${1:-} ]]; then           # laut devicectl ist nur die JSON-Ausgabe stabil
  LIST=$(mktemp); xcrun devicectl list devices -q -j $LIST
  DEVICE=$(python3 -c 'import json,sys
for d in json.load(open(sys.argv[1]))["result"]["devices"]:
    if d["hardwareProperties"].get("platform") == "iOS" and d["connectionProperties"].get("pairingState") == "paired" \
       and d["connectionProperties"].get("tunnelState") != "unavailable":
        print(d["identifier"]); break' $LIST); rm -f $LIST
else
  DEVICE=$1
fi
[[ -n $DEVICE ]] || { echo "Kein iPhone gefunden (USB, entsperrt, „Diesem Computer vertrauen“?)" >&2; exit 1; }
xcrun devicectl device install app --device $DEVICE $APP
xcrun devicectl device process launch --device $DEVICE --terminate-existing $BUNDLE
