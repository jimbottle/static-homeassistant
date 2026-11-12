# Static Radio for Home Assistant (unofficial)

An unofficial Home Assistant integration for [Static Radio](https://staticradio.app), the
live radio dial made of podcasts. It is not affiliated with, endorsed by or supported by
Static, and it is not endorsed by Home Assistant.

**Status: it does not play anything yet.** The parts that need no audio are built and
tested; the part that plays a station is not. There is no release to install.

## What works today

Installed by hand as a development copy, the integration:

- adds a sensor for every station on Static's dial with the show that is on, the episode,
  a link to the episode's page, the show's **Support** link when it has one (never on a
  community bulletin) and the cover art;
- adds a listeners sensor per station with Static's live count. These start disabled, and
  Static is asked for counts only once you switch one on;
- lists the stations in the media browser, by name. They cannot be started from there yet;
- raises a repair notice if Static changes its schedule format.

[examples/now-playing-card.yaml](examples/now-playing-card.yaml) is a dashboard card that
shows every station with its Episode and Support links.

It asks Static for the schedule at most every 15 minutes and for listener counts at most
once a minute, and a show's own feed at most once a day.

An English sentence file for voice control ships in the integration but does nothing yet:
the handlers behind it arrive with playback.

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

The tests run offline, against a saved copy of Static's schedule and Home Assistant
Core 2026.7.2. `tools/deploy-dev.sh` copies the integration to a development Home
Assistant over SSH; it names no host of its own and restarts nothing.

Changes land as squash-merged pull requests with Conventional Commit titles.
[CLAUDE.md](CLAUDE.md) is the contributor guide, including the rules from
Static that every change has to keep.

## Releasing

Nothing has been released. Releases will be `vX.Y.Z` tags cut with
`scripts/release.sh`, with a changelog generated from the commit history and a zip of the
integration, built by `scripts/dist.sh`, attached to each one.

## License

MIT, see [LICENSE](LICENSE). The license covers this repo's code. It does not cover
Static's name, logo, schedule or the shows.

## Credit

Static Radio is made by the people at [staticradio.app](https://staticradio.app). The
stations, the schedule and the shows are theirs and the podcasters'. Static's name and logo
are theirs and are not covered by this repo's license.
