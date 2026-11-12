#!/usr/bin/env bash
# Installs the pre-commit and commit-msg hooks (Conventional Commit subject,
# 72-character subject, no AI bylines, gitleaks, ruff).
#
# A plain clone: this is `pre-commit install`. A clone where another tool owns
# core.hooksPath (the owner's issue tracker and review daemon do): pre-commit
# refuses to install there, so this chains `pre-commit hook-impl` onto the end
# of the hooks in that directory instead. Idempotent.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
command -v uv >/dev/null || { echo "uv is not on PATH (https://docs.astral.sh/uv/)" >&2; exit 1; }
# pre-commit comes from the project's own environment (the dev group in
# pyproject.toml), so the hooks do not depend on a pre-commit that happens to
# be on PATH and may not run under this repo's pinned Python.
uv sync --frozen --quiet

HOOKS_DIR="$(git config --get core.hooksPath || true)"
if [ -z "$HOOKS_DIR" ]; then
  uv run --frozen pre-commit install --hook-type pre-commit --hook-type commit-msg
  exit 0
fi

MARK='# --- BEGIN PRE-COMMIT FRAMEWORK ---'
for type in pre-commit commit-msg; do
  hook="$HOOKS_DIR/$type"
  [ -f "$hook" ] || printf '#!/usr/bin/env sh\n' > "$hook"
  if grep -qF "$MARK" "$hook"; then echo "kept: $hook"; continue; fi
  cat >> "$hook" <<HOOK

$MARK
# core.hooksPath points at this directory, so git ignores what
# \`pre-commit install\` would write under .git/hooks. Run through uv, not an
# absolute interpreter path, so the hook works from any checkout or worktree.
if command -v uv >/dev/null 2>&1; then
  exec uv run --frozen --quiet pre-commit hook-impl \\
    --config=.pre-commit-config.yaml \\
    --hook-type=$type \\
    --hook-dir "\$(cd "\$(dirname "\$0")" && pwd)" -- "\$@"
fi
# --- END PRE-COMMIT FRAMEWORK ---
HOOK
  chmod +x "$hook"
  echo "chained: $hook"
done
