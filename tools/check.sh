#!/bin/sh
# Alle Checks, die vor jedem Commit und in CI grün sein müssen (CLAUDE.md).
set -eu
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run lint-imports
uv run --extra app pytest -q
