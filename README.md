# Static Radio for Home Assistant (unofficial)

An unofficial Home Assistant integration for [Static Radio](https://staticradio.app), the
live radio dial made of podcasts. It is not affiliated with, endorsed by or supported by
Static, and it is not endorsed by Home Assistant.

**Status: not yet working.** This repo is at the start of the build. Nothing here plays
audio yet, and there is nothing to install.

## What it will do

Play Static's live stations on Home Assistant media players, with the Home Assistant Voice
Preview Edition as the reference device: tune by voice, from a dashboard, or by hand, and
land on the same live moment every other listener is hearing.

It follows the ground rules in Static's [developer guide](https://staticradio.app/developers):
every episode streams from the show's own host, nothing is cached or re-hosted, bulletins
play as scheduled, each show's Support link is shown, and it stays live, with no seeking or
skipping.

## Development

```bash
uv sync                      # Python 3.14, pinned in .python-version and uv.lock
scripts/install-hooks.sh     # pre-commit and commit-msg hooks, once
scripts/verify.sh            # ruff, format, mypy strict, pytest
```

Changes land as squash-merged pull requests with Conventional Commit titles.
[CLAUDE.md](CLAUDE.md) is the contributor guide, including the rules from
Static that every change has to keep.

## Releasing

Nothing has been released. Releases will be `vX.Y.Z` tags cut with
`scripts/release.sh`, with a changelog generated from the commit history.

## Credit

Static Radio is made by the people at [staticradio.app](https://staticradio.app). The
stations, the schedule and the shows are theirs and the podcasters'. Static's name and logo
are theirs and are not covered by this repo's license.
