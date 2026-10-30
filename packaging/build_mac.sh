#!/bin/sh
# Compatibility: builds app + DMG as before (--no-dmg: app only). The actual build: tools/build_app.py
set -eu
cd "$(dirname "$0")/.."
[ "${1:-}" = "--no-dmg" ] && set -- --no-pack
exec uv run --extra app python tools/build_app.py "$@"
