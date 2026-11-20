# The dial on custom firmware

The integration works on a Home Assistant Voice Preview Edition left on its
stock firmware: voice, the dashboard and, after a double press, the volume
dial all tune it (see [setup.md](setup.md), step 8). Nothing here is needed.

Flashing the overlay in [`esphome/static-dial.yaml`](../esphome/static-dial.yaml)
gives the dial the full experience. This page says what changes, how to
install it, how to go back, and what has been tried.

**Status: built, not yet run on a device.** The overlay compiles in full
against stock firmware 26.9.0 (see [Tried so far](#tried-so-far)). It has not
been flashed, so everything below about how it behaves comes from reading the
firmware's source and from tests of the integration's side.

## What changes

| | Stock firmware | With the overlay |
|---|---|---|
| Tuning by the dial | A double press, then turn within 8 s | Turn, whenever Static is playing on the speaker |
| Volume while Static plays | Turn | Hold the button and turn (or the other way round: see "hold to tune" below) |
| Volume the rest of the time | Turn | Turn, unchanged |
| The ring while tuning | The volume display, which jumps as the volume is moved and put back, and shows red if the volume touches 0 | One LED for the station the dial is on, for 3 s after the last click |
| A click | Read from a volume change, about 0.15 s later | Reported by the device itself |
| Firmware updates | Offered by the device | Rebuild and flash the overlay for each stock release |

Everything else is the stock firmware's own configuration, pulled from its
release tag and not edited: the wake word, the voice assistant, timers, the
mute switch, the button's presses and playback.

The overlay adds three entities to the device:

- **Static dial** (switch). On: a turn tunes. It is off whenever the device
  boots. The integration turns it on while Static plays on the speaker and off
  when it stops, for a speaker given its "Button press" entity on the
  Configure screen.
- **Static dial: hold to tune** (switch). On: a plain turn stays the volume
  and a turn with the button held tunes.
- **Static dial station** (number). The dial's place, 1 for the first station
  and 0 for none. The integration sets it; the ring lights that LED.

With "Static dial" on, a turn with the button held no longer changes the
group volume or the ring's colour, as it does on stock firmware.

## What you give up

- The device's own firmware update entity, and Bluetooth set-up of Wi-Fi.
  The overlay is built from the stock configuration without the factory
  image's extras, and gets its Wi-Fi from a file at build time.
- Stock updates as they come out. A new stock release needs the overlay's
  three pins moved to it (the release tag, the `voice_kit` commit and the
  ESPHome release), a compile, and a flash.

## Install

1. Copy `esphome/secrets.example.yaml` to `esphome/secrets.yaml` and fill in
   your Wi-Fi. The file is ignored by git.
2. Compile, from the `esphome/` folder:
   `uvx esphome==2026.6.0 compile static-dial.yaml`
3. Flash over the network, with the device on stock firmware and on Wi-Fi:
   `uvx esphome==2026.6.0 upload static-dial.yaml --device <the device's address>`
4. In Home Assistant, the device shows "Static dial", "Static dial: hold to
   tune" and "Static dial station". If the integration's Configure screen
   already has the speaker's "Button press" entity, reload the integration.

## Going back to stock

- **Over USB:** connect the device to a computer and use the stock firmware's
  own web installer at <https://esphome.github.io/home-assistant-voice-pe/>.
  This works whatever the device is running.
- **Over the network:** download `home-assistant-voice-esp32s3.ota.bin` from
  the stock release at
  <https://github.com/esphome/home-assistant-voice-pe/releases> and send it
  with `uvx esphome==2026.6.0 upload static-dial.yaml --device <address> --file <the image>`.

Then remove the three "Static dial" entities Home Assistant no longer finds.
The integration goes back to the double press by itself: it looks for the
"Static dial" switch each time it loads.

## Tried so far

| What | Status |
|---|---|
| The overlay passes ESPHome 2026.6.0's config check | Done 2026-11-20 |
| The overlay compiles in full with ESPHome 2026.6.0 | Done 2026-11-20: 2,852,224 bytes, 35.1 % of flash, 21.6 % of RAM |
| The integration takes the overlay's clicks, switches "Static dial" with playback and sets the station number | Tests only |
| Flashed on a device; the dial, the ring and everything stock checked by hand | Not yet |
| Going back to stock, by USB and over the network | Not yet |
