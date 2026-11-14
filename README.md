# Static Radio for Home Assistant (unofficial)

An unofficial Home Assistant integration for [Static Radio](https://staticradio.app), the
live radio dial made of podcasts. It is not affiliated with, endorsed by or supported by
Static, and it is not endorsed by Home Assistant.

**Status: it plays, as a development copy.** A tuned speaker gets its station live, and
it has been heard doing so on one Home Assistant Voice Preview Edition through Music
Assistant. There is no release to install yet, and nothing here has been tried on other
speakers.

## What works today

Installed by hand as a development copy, the integration:

- plays a station on a speaker you choose on the Configure screen, starting at the live
  moment every other listener is hearing. A speaker that is a Music Assistant player is
  asked through Music Assistant; any other media player is asked directly;
- tunes three ways: a station selector per speaker for a dashboard, a `static_radio.tune`
  action (by station name, by a step along the dial, or back to the last station), and
  voice intents for play, tune, next and previous;
- says the station's name at the start of the stream, in your assistant's own voice, while
  the episode is fetched. This can be switched off, and is left out when you tune by voice
  because the assistant has just said it;
- tunes a speaker again if it drops off the network while playing and returns within ten
  minutes;
- adds a sensor for every station on Static's dial with the show that is on, the episode,
  a link to the episode's page, the show's **Support** link when it has one (never on a
  community bulletin) and the cover art;
- adds a listeners sensor per station with Static's live count. These start disabled, and
  Static is asked for counts only once you switch one on;
- lists the stations in the media browser, by name, each playable on a speaker and each
  opening to show what is on with its Episode and Support links;
- raises a repair notice if Static changes its schedule format.

[examples/now-playing-card.yaml](examples/now-playing-card.yaml) is a dashboard card that
shows every station with its Episode and Support links.

An English sentence file for voice control ships in the integration. It is not switched on
for you: copy `sentences/en.yaml` into your `custom_sentences/en/` folder to use it.

## How it keeps to Static's rules

It follows the ground rules in Static's [developer guide](https://staticradio.app/developers):

- Every episode is fetched from the show's own host, by its own address every time, by
  your own Home Assistant, as you listen. Nothing is written to disk, cached or re-hosted.
- It stays live. There is no seeking, skipping or pausing: stopping and starting again
  lands on the live moment. Bulletins play where they fall, for at most 30 seconds.
- An episode that fails is tried again twice, then left until the next one starts on time.
- It asks Static for the schedule at most every 15 minutes and for listener counts at most
  once a minute, and a show's own feed at most once a day.
- Each show's Support link is shown wherever what is playing is shown.

Home Assistant decodes each episode and sends the speaker one continuous stream, because
the reference speaker cannot start a file partway through. The stream's address works only
from your home network and holds a long random key for each speaker.

## Timing

A speaker makes its first sound some time after it is handed a stream, so each speaker has
a lead on the Configure screen: how far ahead of live its stream starts. Measured on a
Voice Preview Edition, the delay was 1.33 s through Music Assistant (20 starts within
0.08 s of each other, and 0.04 s of drift over two hours) and about 0.3 s on its own
ESPHome player. The default lead is 1300 ms.

A stream opens with a little over three seconds the speaker can start on at once, then the
episode joins at the live moment. A slow show host makes that opening longer; it never
makes the stream late.

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
