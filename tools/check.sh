#!/bin/sh
# All checks that must be green before every commit and in CI (CLAUDE.md).
set -eu
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run --extra app pyright
uv run lint-imports
uv run --extra app pytest -q
