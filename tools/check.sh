#!/bin/sh
# Alle Checks, die vor jedem Commit und in CI grün sein müssen (CLAUDE.md).
# pyright und pytest brauchen PySide6, daher einmal mit dem Extra „app“ synchronisieren.
set -eu
cd "$(dirname "$0")/.."
uv sync --extra app
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pyright
uv run --no-sync lint-imports
uv run --no-sync pytest -q
