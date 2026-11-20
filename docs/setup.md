# Setup, step by step, and what has been verified

Each step of setting the integration up, how to tell that it worked, and
whether that has been seen working by hand. "Verified" means a person did the
step on the reference install and saw the result; a passing test alone is
recorded as "tests only". Update the last column whenever a step is tried.

Reference install: Home Assistant Core 2026.7.2, Music Assistant 2.10.8, one
Home Assistant Voice Preview Edition on stock firmware 26.9.0, played through
its Music Assistant player.

## Install

| # | Step | How to tell it worked | Status |
|---|---|---|---|
| 1 | Copy `custom_components/static_radio/` into your configuration's `custom_components/` and restart Home Assistant. | The log has "Setup of domain static_radio" and no error from it. | Verified 2026-11-19 |
| 2 | Settings → Devices & services → Add integration → **Static Radio (unofficial)**. | The integration appears, with a sensor for each of Static's stations showing the show that is on. | Verified 2026-11-19 (nine stations) |
| 3 | Give Home Assistant an address on your home network (Settings → System → Network). | A tuned speaker makes sound. Without the address, tuning fails with a message saying so. | Verified 2026-11-15 (sound); the failure message is tests only |

## The Configure screen

Settings → Devices & services → Static Radio (unofficial) → **Configure**.

| # | Step | How to tell it worked | Status |
|---|---|---|---|
| 4 | Choose the speakers that can be tuned. | Each one gets a "… station" selector entity. Choosing a station in it starts that station on the speaker within a few seconds. | Verified 2026-11-19 |
| 5 | Choose the phone a Support link is sent to, or none. | With a show on air that has a Support link, "send me the Static support link" puts a notification on that phone. | Not yet verified. On 2026-11-19 no show with a link was on air; the "has no Support link" reply was heard |
| 6 | Leave "Say the station's name when tuning" on, or switch it off. | Tuning from the selector opens with the station's name, spoken. Tuning by voice does not repeat it. | Verified 2026-11-19 |
| 7 | Set each speaker's lead. Keep 1300 ms for a Voice Preview Edition through Music Assistant. | The speaker is within about a second of Static's web player on the same station. | Verified 2026-11-18: 1.07 s behind the timeline (median of 9 stations); the web player was 0.90 s behind |
| 8 | Optional: give a speaker its "Button press" entity, to tune with its volume dial. | A double press of the button, then one click of the dial, moves one station along and says its name; the volume ends where it began. Ten seconds later the dial is volume again. | Saved and loaded: verified 2026-11-19. Browsing one click at a time, each name said and the station stopped on loaded: verified 2026-11-20. A fast turn, and a turn on from the last station round to the first: not yet verified on stock firmware (the second was verified with the custom firmware, 2026-11-20) |

## Voice

| # | Step | How to tell it worked | Status |
|---|---|---|---|
| 9 | Copy `custom_components/static_radio/sentences/en.yaml` to `custom_sentences/en/static_radio.yaml` and reload conversation. | "Play Static News" starts News and answers "Playing Static News." without waiting on a language model. | Verified 2026-11-19: all nine stations by name, next, previous and what's on, spoken at the speaker |
| 10 | Say your usual "stop", then "play" or "play Static". | It stops; it comes back on the same station at the live moment. | Verified 2026-11-19 |

## Optional extras

| # | Step | How to tell it worked | Status |
|---|---|---|---|
| 11 | Enable a station's listeners sensor. | It shows Static's live count and updates no more than once a minute. | Tests only |
| 12 | Add `examples/now-playing-card.yaml` to a dashboard. | Every station is listed with its Episode link, and a Support link when the show has one. | Tests only |
| 13 | Open Media → Static Radio in the media browser. | Each station is listed and plays on a chosen speaker. | Not recorded since 2026-11-14; try again before a release |

## The full dial experience (optional custom firmware)

Every step above works on a speaker left on its stock firmware, and that is
the install this page verifies. On a Voice Preview Edition the dial is at its
best with the overlay in `esphome/` flashed: a turn tunes with no double
press, the ring lights one LED for the station the dial is on, and the volume
display no longer jumps. [dial-firmware.md](dial-firmware.md) says what
changes, what you give up, and how to go back.

| # | Step | How to tell it worked | Status |
|---|---|---|---|
| 14 | Optional: compile and flash `esphome/static-dial.yaml` (see [dial-firmware.md](dial-firmware.md)). | The device gains "Static dial", "Static dial: hold to tune" and "Static dial station". With Static playing, a turn of the dial says the next station and one LED moves with it; with Static stopped, a turn is the volume. | Verified 2026-11-20: each name said and one LED moving with it, round the whole dial, and the volume by a held turn. "Static dial: hold to tune": not yet verified |

## Known setup snags

- With more than one speaker chosen, voice has to work out which one you
  mean, and no sentence names a speaker. It takes the speaker that is the
  same device as the one spoken to, then the only speaker in the same area.
  A Music Assistant player can be a separate device from the microphone, so
  give the two the same area, or the reply is "More than one speaker can play
  Static, so say which one." Tests only: the reference install has one speaker.
- Tuning by the dial starts from the station the speaker is on. The dial is a
  circle, as Static's own is: on from the last station is the first.
- A show's Support link exists only for some shows. On 2026-11-19 ten of the
  day's 184 episodes had one, so step 5 can only be tried while one of them
  is on air.
