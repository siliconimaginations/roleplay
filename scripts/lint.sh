#!/usr/bin/env bash
# Run all linters locally before pushing.
# Usage: bash scripts/lint.sh
# Requires: uv installed, dev dependencies synced (uv sync --extra dev)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Ruff: lint ==="
uv run ruff check .

echo "=== Ruff: format check ==="
uv run ruff format --check .

echo "=== Mypy: type check ==="
uv run mypy src/

echo ""
echo "✅  All checks passed."
