# Static Radio for Home Assistant (unofficial)

The contributor guide for this repo, for people and for coding agents.

An unofficial Home Assistant custom integration, domain `static_radio`, that
plays [Static Radio](https://staticradio.app)'s live stations on Home Assistant
media players, with the Voice Preview Edition as the reference device. Static
agreed to an unofficial build on conditions; the rules below are those
conditions and they do not bend.

Static's [developer guide](https://staticradio.app/developers.md) is the
authority. Read it in full, and follow the agent prompt inside it, before
writing code that talks to Static. Where this file and the guide disagree, the
guide wins: stop and say so.

## Stack

- Python 3.14, pinned in `.python-version` and `uv.lock`. Home Assistant Core
  2026.7.2 is the release this is built and tested against.
- Standard library plus what Home Assistant ships, ffmpeg included. Ask before
  adding a dependency Home Assistant does not ship.
- The timeline and MP3 seek modules import nothing from Home Assistant, so
  their tests run anywhere.

## Quick start

```bash
uv sync                      # creates .venv on the pinned Python
scripts/install-hooks.sh     # pre-commit and commit-msg hooks, once
scripts/verify.sh            # the one gate: ruff, format, mypy strict, pytest
```

Run `scripts/verify.sh` before calling any task done. If a test fails in a way
that makes no sense, check the interpreter first (`uv run python -V`).

## Rules that do not bend

- From Static, request only `schedule-wear.json`, `/api/counts`, and the audio
  and art addresses the schedule itself lists, all with `GET`. Never call any
  other Static host or path, including the presence and bulletin-slot endpoints
  visible in Static's own reference ports. One exception, for development
  only: Static's guide publishes a read-only MCP server at
  `https://staticradio.app/mcp` for AI assistants, and `.mcp.json` adds it so
  a coding agent can cross-check what is live. The integration's own code
  never calls it.
- Stream each episode from the show's own host. Request `audioUrl` itself every
  time, never the address it redirects to.
- Never write audio to disk, cache it, re-host it, or serve it to anyone but
  the tuned speaker. Fixtures are schedule files and generated test tones, never
  episode audio.
- Keep it live: no seeking, skipping, rewinding or episode selection. A resume
  re-tunes to the live moment.
- Play bulletins where they fall, for at most 30 s. Never skip, mute or replace
  one.
- Show a link labeled "Support" to `supportUrl` wherever now-playing is shown,
  beside the `episodeUrl` link, and never on a bulletin.
- Timing never uses `startOffsetSeconds`; it leaves out the 1.5 s gaps.
- Fetch the schedule at most every 15 minutes with `If-None-Match`, listener
  counts at most once a minute, a show's feed at most once a day.
- Every outbound request carries `User-Agent: HomeAssistant-Static/<version>`.
- "unofficial" stays in the repo description, the README title and the
  integration name. Credit and link staticradio.app.
- Do not contact Static. Messages to Static are drafted for the owner to send.
- This repo is public. No house-specific entity id, device id, address,
  hostname or SSH alias, no household transcript, no name from Static's team
  and nothing from correspondence with Static goes into a tracked file. Issue
  tracker data, session state and planning notes are local and gitignored.

## Current scope

Voice, dashboard, button and (opt-in, custom firmware) dial control of one
station stream per speaker. Not in the first release: synchronized multi-room
groups, languages other than English, the default HACS store, a Music Assistant
provider. Do not build these without asking.

## Conventions

- One job per file, named for the job. Each file opens with a plain-language
  comment saying what it does and why, and a port names the file it ports.
- Units in constant names, the measurement in the comment (`GAP_MS`,
  `BULLETIN_CAP_MS`, `LEAD_MS`), matching Static's reference ports.
- Tests live in `tests/` and run offline. Tests that need the Home Assistant
  harness are kept apart from the pure-Python ones.
- Nothing under `spikes/` is reused in the integration.
- Spoken replies are one sentence, end in a period, and never end in a
  question mark. Sentence lists are prefixed `static_`.

## Issue tracking and sessions

The owner tracks work in a local issue tracker (bd, with wyk on top). Its data
never leaves the owner's machines: do not run `bd dolt push` or add a remote
for it. Where the generated block below says "push", it means push your
branch and open or update the pull request. Nothing is pushed to `main`.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->


<!-- BEGIN WYK CONVENTIONS v:1 -->
## wyk — planning & handoff over bd

This repo uses **wyk**, a view + handoff layer over **bd (beads)**. "Plan
it in wyk" = **file the plan as bd issues** (deps via `bd dep add`), not
markdown/TodoWrite. File with **`wyk create`** (same flags as `bd create`,
forwarded verbatim) — it also stamps the Claude session so the TUI's
Session column traces work back to a conversation. A PreToolUse hook
blocks raw `bd create` and tells you to switch; that's expected — just
re-run as `wyk create`.

**Owner column** — whose move it is, label-driven (NOT bd's owner/assignee):
- `human` → **HUMAN** (a human must act).
- `agent-handoff` → **AGENT-HANDOFF**: another agent owns it; don't touch,
  a human coordinates. Excluded from `wyk inbox`.
- agent task blocked by a `human`-flagged dep → **HUMAN-BLOCK** (skip it).
- checked out by an agent → **@<owner>** (a live lease); lapsed → **EXPIRED**.
- else → **AGENT** (the default; a null owner is never blank — so a task
  that needs a human MUST be handed off, or the human never sees it).

**Hand off to a human**: `wyk handoff <id>` (or `wyk handoff -create "<title>"`)
sets `human` + writes the runbook. Never hand-roll labels; `-a`/`--claim`
are bd's status, not the badge. Decide first what you're asking for: a
**task** (the human DOES something → the runbook needs `## Steps`, the
directions they follow) or a **question** (the human ANSWERS/DECIDES →
`## Question`, with options + your recommendation). `wyk handoff` refuses a
runbook with neither. `wyk handoff -template [-question]` prints the skeleton.

**Pick up work**: `wyk next -claim` — your held work, then the inbox (items
bounced back to you — WORK them), then ready work — and checks the top pick
out as an expiring lease so other agents in this repo leave it alone. Work on
your own branch. A `@<owner>` row is another agent's: don't touch it;
`EXPIRED` is claimable. `wyk conventions` prints the full contract.

**Something wrong? Act — don't shrug.** If a wyk/bd command errors, a
convention looks broken, or the workflow rubs wrong, file a bd issue (with
an owner) and fix or hand it off — don't route around it silently.
Friction with wyk is product data; surfacing it is the job.
<!-- END WYK CONVENTIONS -->

## Version control

This repo follows the Raylytics Version Control Standard, tier 1, stack python. Source: STANDARD.md in the `raylytics` repo (2026-10-27); cite its section in a commit body when a rule decides something.

- Branch from `main` as `type/short-kebab`. Every change lands as a squash-merged PR with CI green; the PR title is the Conventional Commit subject. Agent work always goes through a PR, and the owner merges.
- Commits follow Conventional Commits 1.0: `type(scope): subject`, subject at most 72 characters, imperative, no trailing period, then a body that says why and gives the measured number where there is one. Types: feat, fix, perf, refactor, style, docs, test, build, ci, chore, revert. Scopes here: `timeline`, `seek`, `api`, `stream`, `tuner`, `sensor`, `voice`, `dial`, `spike`, `docs`, `ci`, `deps`, `release`, `vc`.
- Never add `Co-Authored-By`, `Generated with`, or any AI byline; the commit-msg hook refuses them. Never use `--no-verify`. Never force-push `main`.
- Secrets never enter the tree; gitleaks runs in pre-commit and CI. This project needs no credential: Static's interface is public.
- Commands: `scripts/install-hooks.sh` once; lint and test `scripts/verify.sh`; release `scripts/release.sh X.Y.Z`, run by the owner. Agents never bump a version, tag, or write a changelog entry; `CHANGELOG.md` is generated by git-cliff at release.
- Do not commit `.claude/settings.local.json`, `QUEUE.md`, `HANDOFF*`, `.beads/`, scratch notes or session logs.
