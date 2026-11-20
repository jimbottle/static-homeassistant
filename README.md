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
- optionally turns a speaker's own volume dial into a tuning dial. Give a speaker its
  "Button press" entity on the Configure screen (a Voice Preview Edition on stock firmware
  has one) and a double press of its button makes each click of the dial one station along
  Static's dial for the next 8 seconds. The speaker says each station's name as the dial
  reaches it and loads the one you stop on. The volume is put back after each click, so
  expect a brief flicker of it. Left alone, the dial is the volume dial again.
  Optional custom firmware for the Voice Preview Edition makes this the full
  experience: no double press, and an LED on the ring for the station the dial
  is on (see [docs/dial-firmware.md](docs/dial-firmware.md); checked by hand on one
  device);
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

## Install

There is no release yet. Until there is, install it by hand:

1. Copy `custom_components/static_radio/` from this repo into the `custom_components/`
   folder of your Home Assistant configuration, then restart Home Assistant.
2. Settings → Devices & services → Add integration → **Static Radio (unofficial)**. It
   needs no account.
3. Open the integration's **Configure** screen and choose the speakers that can be tuned
   to Static, the phone a show's Support link is sent to (or none, to have it read out),
   and whether the station's name is spoken at the start of the stream.
4. The next screen sets each speaker's lead (see [Timing](#timing)). The default,
   1300 ms, was measured on a Voice Preview Edition through Music Assistant.

5. Optionally, the last screen gives a speaker its "Button press" entity, so that its
   volume dial tunes for a few seconds after a double press.

[docs/setup.md](docs/setup.md) lists every setup step with how to tell it worked and
whether it has been verified by hand.

Each speaker chosen gets a station selector, which you can put on a dashboard. Once a
release is out, it installs through HACS as a custom repository instead.

Home Assistant has to have an address on your home network (Settings → System → Network),
because the speaker fetches its stream from Home Assistant.

### Tested on

| | Version |
|---|---|
| Home Assistant Core | 2026.7.2 (Python 3.14.6, ffmpeg 8.1) |
| Music Assistant | 2.10.8 |
| Speaker | Home Assistant Voice Preview Edition, stock firmware 26.9.0 (ESPHome 2026.6.0) |

Other speakers should work through Music Assistant or as plain media players, but none has
been tried.

## Voice

An English sentence file ships in the integration. It is not switched on for you: copy
`custom_components/static_radio/sentences/en.yaml` to `custom_sentences/en/static_radio.yaml`
in your configuration, then reload conversation (Developer tools → YAML) or restart.

Every sentence has the word "Static" in it. Spoken to a speaker, it understands:

- "play Static", and "play Static News" or "tune Static to Nature" for a station;
- "next Static station" and "previous Static station";
- "what's on Static";
- "what's the Static support link", and "send me the Static support link", which goes to
  the phone chosen on the Configure screen.

Station names are Static's own and no others: no nicknames. No sentence names a speaker.
With one speaker chosen on the Configure screen, the music goes to it. With more than one,
it goes to the speaker you spoke to: the one that is the same device as the microphone, or
failing that the only one in the same area. Give each voice device and its speaker the same
area in Home Assistant, or it will answer that it cannot tell which speaker you mean. This
matters most through Music Assistant, whose player can be a separate device from the
microphone. It has been tried with one speaker only.

What it leaves alone, on purpose:

- anything without "Static" in it. "Next station", "play the news" and "play some jazz"
  stay with whatever your home already does with them;
- "play Static on the speaker". Home Assistant's own "play … on <speaker>" sentence takes
  that one, and searches your music for "static";
- stop, pause and resume. Your usual "stop" stops Static, and playing it again tunes to
  the live moment. There is no Static sentence for them because there is nothing to resume
  from: Static is live.

## How it keeps to Static's rules

It follows the ground rules in Static's [developer guide](https://staticradio.app/developers):

- Every episode is fetched from the show's own host, by its own address every time, by
  your own Home Assistant, as you listen. Nothing is written to disk, cached or re-hosted.
- A speaker is given at most a minute more than it has had time to play. A player keeps a
  few seconds in hand to play smoothly; nothing is fetched to be kept.
- It stays live. There is no seeking or skipping: pausing or stopping and then playing
  again lands on the live moment. Bulletins play where they fall, for at most 30 seconds.
- An episode that fails is tried again twice, then left until the next one starts on time.
  A try that played a minute or more before the host stopped does not count, because some
  hosts cut a long download every few minutes.
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
