#!/bin/sh
# Kompatibilität: baut wie früher App + DMG (--no-dmg: nur App). Eigentlicher Build: tools/build_app.py
set -eu
cd "$(dirname "$0")/.."
[ "${1:-}" = "--no-dmg" ] && set -- --no-pack
exec uv run --extra app python tools/build_app.py "$@"
