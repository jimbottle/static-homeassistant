# Review findings

Valid findings from the automated review of each commit, with the pattern
behind each. Read "Recurring patterns" before committing.

## Recurring patterns

- **A check that runs is not a check that checks.** Before relying on a gate,
  make it fail once on purpose. The gitleaks hook passed in CI because it had
  nothing to scan.
- **A stated rule and a tracked file must agree.** When a file is an exception
  to a rule in CLAUDE.md, the rule names the exception and says why.
- **A shared template is a starting point.** Read each copied file against
  what this repo actually is before committing it.

## Findings

| Date | Review | Finding | Fix |
| --- | --- | --- | --- |
| 2026-11-11 | 7196 | The gitleaks pre-commit hook scans only the staged index, so the CI job that runs pre-commit on a fresh checkout scanned nothing | CI scans the commits with the gitleaks action |
| 2026-11-11 | 7196 | `scripts/release.sh` bumped `pyproject.toml`, a version nothing reads, and would have tagged a release while the integration's manifest kept the old one | The script bumps the integration's `manifest.json` and refuses to release until one exists |
| 2026-11-11 | 7196 | `scripts/verify.sh` labelled every failed step `uv:run`, so the verdict line did not say which gate failed | Each step is named |
| 2026-11-11 | 7197 | `.mcp.json` pointed agents at a Static path the stated rules forbade, with no exception written down | The rule and the review guidelines name the exception: the MCP server Static's guide publishes for assistants, for development only |
| 2026-11-11 | security check | The copied gitleaks allowlist carried patterns for secrets that exist in other repos, not this one | The allowlist is cut to this repo's test fixtures |
