# Phase 0 results

What was checked before any code that plays audio was written, with the date,
the versions and the source of each fact. Numbers and sources only: no
recordings, no transcripts and nothing that identifies a house.

Versions this build is checked against: Home Assistant Core 2026.7.2, Voice
Preview Edition firmware 26.9.0, Music Assistant 2.10.8.

## Do the shows' hosts serve a ranged request? Measured 2026-11-12 UTC

Tuning in mid-episode needs a `Range` request to the show's own host. One
request per station, for one byte (`Range: bytes=0-0`) of the episode live at
that moment, to `audioUrl` itself, with `User-Agent: HomeAssistant-Static/0.0.0`
and `Accept-Language: en`. Nothing was played, converted or kept.

| Station | Format | First host | Redirects | Status | Size reported |
| --- | --- | --- | --- | --- | --- |
| Nature | MP3 | anchor.fm | 1 | 206 | yes |
| Work | MP3 | mcdn.podbean.com | 1 | 206 | yes |
| Learn | MP3 | anchor.fm | 1 | 206 | yes |
| News | MP3 | traffic.omny.fm | 2 | 206 | yes |
| Weird | MP3 | episodes.castos.com | 0 | 206 | yes |
| Unite | M4A | anchor.fm | 1 | 206 | yes |
| Make | MP3 | traffic.libsyn.com | 1 | 206 | yes |
| SHEILA B | MP4 | s3.amazonaws.com | 0 | 206 | yes |
| Sleep | MP3 | www.podtrac.com | 2 | 206 | yes |

- All nine answered `206` with a `Content-Range` giving the file's size, under
  this project's User-Agent. None refused the range or the User-Agent.
- Seven of nine pass through at least one redirect, two through two. The
  request always goes to `audioUrl`, never to where it redirects.
- Two stations were on MP4 audio at the time, not one: SHEILA B and an
  episode on Unite. MP4 handling is not a single-station special case.
- This is one episode per station on one evening. A host that refuses ranges
  can still turn up; the guide's rule for that case (start from the top)
  stays in the design.

## The schedule on 2026-11-11

Read from the copy saved in `tests/fixtures/`.

- `schema_version` 8, nine stations, 4 to 51 episodes per loop.
- No episode carried `supportUrl`. The guide added the field on 2026-11-11
  "from the next schedule build on", so until that build arrives the Support
  link comes from the guide's fallback: the first channel-level
  `podcast:funding` tag in the show's feed, each feed fetched at most once a
  day.
- Seven stations carry bulletins; SHEILA B and Sleep had none.

## Timeline and seek parity. Measured 2026-11-11

- `schedule.py` against the guide's `liveNow()` and the reference's
  `t/live_test.pl`: 77 instants across nine stations, all equal (to the
  millisecond and to the printed second).
- `mp3seek.py` against the reference's `t/mp3seek_test.pl`: 45 answers on five
  generated files, all equal.

## The firmware build behind 26.9.0. Checked 2026-11-11

The dial needs an ESPHome overlay on the stock firmware. To rebuild what the
device runs, the overlay has to be compiled with the same ESPHome release and
the same component source.

| Fact | Value | Source |
| --- | --- | --- |
| ESPHome release that built 26.9.0 | 2026.6.0 | `.github/workflows/build.yml` at tag `26.9.0`: `esphome-version: 2026.6.0` |
| Minimum ESPHome the config accepts | 2026.6.0 | `home-assistant-voice.yaml` at the tag, `min_version` |
| Commit the tag points to | `2644f4c794271d735d182ca7ebf899ed46164f4e`, 2026-09-17 | the tag |
| Where `voice_kit` comes from | the firmware repo's `dev` branch, `refresh: 0s`: not pinned, despite the comment above it | `external_components` in the same file |
| `dev` when the release was published | the tag's own commit | commit history of `dev` up to the release time |
| Last change to `voice_kit` | `52163d6f24d7`, 2026-05-21; none since the release | commit history of `esphome/components/voice_kit` |

So: compile the overlay with ESPHome 2026.6.0 and pin `voice_kit` to the tag's
commit. As of this check `dev` has not changed the component since, but an
unpinned build stops matching the day it does. An earlier validation of the
overlay ran on ESPHome 2026.9.1; it showed the overlay is well-formed, not
that it builds what the device runs.

## The click-track kit. Self-check 2026-11-11

`spikes/click_track/`, served and fetched on one machine: offset read back
-0.010 s; 7,276 s of stream taken in a 3 s connection (not paced); duration
reported as N/A.

## Start delay on the speaker

Not yet measured. This is the spike: 20 tunes on each delivery path, a pause
and resume check, and a two-hour run. The bar is 18 of 20 tunes within 1 s of
the clock, none worse than 2 s, and the two-hour run within 1 s.

## From the research pass of 2026-11-11, not re-checked here

These came from reading upstream source at the tags above before this repo
had code. They are recorded so each can be re-checked before it is relied on.

| Claim | Source to re-check |
| --- | --- |
| Music Assistant plays a plain URL through its builtin provider and treats a stream with no duration as radio, not seekable | `music_assistant/providers/builtin/__init__.py` at 2.10.8, `get_stream_details` |
| It starts a radio stream once 3 s are buffered, keeps a 15 s buffer in memory, and reads at up to 1.03 times playback speed after a 3 s burst | `controllers/streams/constants.py`, `audio_buffer.py` |
| Its Sendspin player has no pause, so a pause becomes a stop, and resuming a radio item reopens the URL | `providers/sendspin/player.py`, `controllers/players/controller.py`, `controllers/player_queues/controller.py` |
| It has no audio cache on disk | `constants.py`: `allow_audio_cache` is read nowhere |
| Home Assistant sends media to an ESPHome speaker through its ffmpeg proxy unless `bypass_proxy` is passed | `homeassistant/components/esphome/media_player.py` at 2026.7.2 |
| The stock firmware's dial turn runs the script `control_volume`; button presses reach Home Assistant through `button_press_event` | `home-assistant-voice.yaml` at 26.9.0 |
