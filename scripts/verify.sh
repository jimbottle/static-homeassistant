#!/usr/bin/env bash
# The one gate for this repo: lint, format, types, tests. Run it from the repo
# root before calling any task done; CI's test job runs the same script.
# The pure-Python tests (timeline, seek, API client) are also run a second time
# in an environment with no Home Assistant in it, to keep the promise that
# they need none. Prints a one-line verdict with the test count as its last line.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# A missing .venv makes `python` fall back to whatever is first on PATH, and
# the failures that follow look like test failures. uv run always uses the
# project's pinned interpreter, so say which one that is.
echo "python: $(uv run --frozen python -V 2>&1)"

fail=""
step() { local name="$1"; shift; echo "== $*"; "$@" || fail="$fail $name"; }
step ruff-check uv run --frozen ruff check .
step ruff-format uv run --frozen ruff format --check .
step mypy uv run --frozen mypy .

# asyncio_mode in pyproject.toml belongs to a plugin this environment lacks.
step pure-without-ha uv run --frozen --isolated --only-group pure \
  pytest -q -p no:cacheprovider -W ignore::pytest.PytestConfigWarning

echo "== uv run pytest -q"
# Shown as it runs, and kept for the verdict line. Held back until the end, a
# run that hangs says nothing at all, including where it is stuck.
log="$(mktemp)"
uv run --frozen pytest -q 2>&1 | tee "$log"; rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || fail="$fail pytest"
tests="$(tail -1 "$log")"
rm -f "$log"

if [ -z "$fail" ]; then echo "VERIFY OK: $tests"; else echo "VERIFY FAILED (${fail# }): $tests"; exit 1; fi
