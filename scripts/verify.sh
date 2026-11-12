#!/usr/bin/env bash
# The one gate for this repo: lint, format, types, tests. Run it from the repo
# root before calling any task done; CI's test job runs the same script.
# Prints a one-line verdict with the test count as its last line.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# A missing .venv makes `python` fall back to whatever is first on PATH, and
# the failures that follow look like test failures. uv run always uses the
# project's pinned interpreter, so say which one that is.
echo "python: $(uv run --frozen python -V 2>&1)"

fail=""
step() { echo "== $*"; "$@" || fail="$fail $1:$2"; }
step uv run --frozen ruff check .
step uv run --frozen ruff format --check .
step uv run --frozen mypy .

echo "== uv run pytest -q"
out="$(uv run --frozen pytest -q 2>&1)"; rc=$?
echo "$out"
[ $rc -eq 0 ] || fail="$fail pytest"
tests="$(printf '%s\n' "$out" | tail -1)"

if [ -z "$fail" ]; then echo "VERIFY OK: $tests"; else echo "VERIFY FAILED (${fail# }): $tests"; exit 1; fi
