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

- **Import a module by the one name it will always have.** A test that reaches
  a file through a second import root works until the package gains an
  `__init__.py`, then the file exists under two names.

- **Text from someone else's server gets a size limit before a pattern.** A
  pattern copied from documentation is written for honest input.

## Findings

| Date | Review | Finding | Fix |
| --- | --- | --- | --- |
| 2026-11-11 | 7196 | The gitleaks pre-commit hook scans only the staged index, so the CI job that runs pre-commit on a fresh checkout scanned nothing | CI scans the commits with the gitleaks action |
| 2026-11-11 | 7196 | `scripts/release.sh` bumped `pyproject.toml`, a version nothing reads, and would have tagged a release while the integration's manifest kept the old one | The script bumps the integration's `manifest.json` and refuses to release until one exists |
| 2026-11-11 | 7196 | `scripts/verify.sh` labelled every failed step `uv:run`, so the verdict line did not say which gate failed | Each step is named |
| 2026-11-11 | 7197 | `.mcp.json` pointed agents at a Static path the stated rules forbade, with no exception written down | The rule and the review guidelines name the exception: the MCP server Static's guide publishes for assistants, for development only |
| 2026-11-11 | security check | The copied gitleaks allowlist carried patterns for secrets that exist in other repos, not this one | The allowlist is cut to this repo's test fixtures |
| 2026-11-11 | 7204 | Tests imported `schedule` as a top-level module through an extra import root, which would have shown the file under two names, and shadowed standard-library modules such as `select`, once the integration package exists | Tests import `custom_components.static_radio.<name>`; the extra import root is gone |
| 2026-11-11 | 7204 | `parse_epoch_ms` went through a float and could land 1 ms short of JavaScript's `Date.parse` on an epoch with fractional seconds | Whole-number arithmetic, with a test |
| 2026-11-11 | 7208 | The spike's README told the reader to save a room recording at the repo root, outside the ignored folder | The documented path is `spikes/out/`, and audio files anywhere under `spikes/` are ignored |
| 2026-11-11 | 7208 | The spike's connection log was described as showing a player's read-ahead; the figure counts audio generated, with seconds of buffering in between | The README says what the log does and does not show |
| 2026-11-11 | 7213 | The gate's second test run, the one without Home Assistant, installed the newest pytest instead of the locked one, so a new release could break the gate with no change here | The pure tests' dependencies are a locked group, installed alone and frozen |
| 2026-11-11 | security check | The pattern that reads a feed's funding tag, taken from the guide as written, went quadratic on a feed of unclosed tags: 0.66 s on 60 KB, and a feed can be megabytes | Only the first 256 KB of a feed is read and every run in the pattern has a limit: 1 ms on 520 KB of the same input |
| 2026-11-11 | 7217 | One lock covered every feed and was held during a fetch, so a slow host would have held up every other show's link | One lock per feed, and a cached answer takes no lock |
| 2026-11-12 | 7236 | The media browser named the show and episode on each station but has no place for the episode and Support links Static asks to see beside now-playing | Entries carry the station's name only; what is on stays on the sensors, which carry both links. Whether a link-less listing would be acceptable is Static's to say |
| 2026-11-12 | 7259 | The Support link's full address was attached only as a response card, which Home Assistant removes from the result a language model is given, so a model could pass on only the shortened spoken form; the what's-on reply attached no links at all | Both replies now carry `episode_url` and `support_url` as whole addresses in the response's slots, which the model's result keeps; a test builds that result and checks them |
