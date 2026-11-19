"""Tuning with a speaker's own volume dial, on firmware that was not changed.

A Voice Preview Edition on stock firmware tells Home Assistant nothing about
its dial but this: each click moves the volume of the device's own media
player by one step of 0.05, and a double press of its button fires a "Button
press" event. This file turns those two facts into a tuning dial:

- A double press arms the dial for ARMED_S. Until then, and after, the dial is
  the volume control it always was.
- While armed, each volume step is taken as one click along Static's dial:
  the volume is put back at once, the clicks are added up, and when the dial
  has rested for REST_S the speaker is tuned that many stations along. Each
  click keeps the dial armed for another ARMED_S.
- Static's dial has two ends and does not wrap, so a long turn stops at the
  first or the last station.

Measured on the reference speaker (firmware 26.9.0): one slow click is one
update of 0.05, and fast clicks arrive as one update of several steps, so
steps are counted from the size of the change and never from the number of
updates. What the dial cannot do this way: turn past volume 0 or 1 (the
device reports nothing there), and tell a click the other way from the volume
being put back when the two cross.

The volume is put back through the speaker chosen on the Configure screen and
not through the device's own player, which a home may keep hands off.

The custom firmware overlay in esphome/ reports real dial clicks and needs
none of this; this file is for a speaker left on stock firmware.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import CONF_DIALS, CONF_SPEAKERS
from .tune import TuneError, async_tune, loaded_entry, tuned_station

if TYPE_CHECKING:
    from . import StaticConfigEntry

_LOGGER = logging.getLogger(__name__)

ARMED_S = 8.0  # how long the dial tunes after a double press, and after each click
REST_S = 0.3  # the dial has stopped when no click has come for this long
STEP = 0.05  # the volume change of one click, of a full range of 1
STEP_SLACK = 0.006  # a player that keeps whole percents rounds a step by up to this
ARM_PRESS = "double_press"  # the button event that arms the dial

_GONE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


def clicks_in(change: float) -> int:
    """How many dial clicks a change of volume is. 0 when it is not a whole number of steps."""
    clicks = round(change / STEP)
    return clicks if abs(change - clicks * STEP) <= STEP_SLACK else 0


def volume_player_for(hass: HomeAssistant, button: str, speaker: str) -> str:
    """The media player whose volume the dial moves: the one on the button's own device.

    Falls back to the speaker itself, which is right for a speaker tuned
    through its own player.
    """
    entities = er.async_get(hass)
    known = entities.async_get(button)
    if known is None or known.device_id is None:
        return speaker
    own = [
        e.entity_id
        for e in er.async_entries_for_device(entities, known.device_id)
        if e.domain == "media_player"
    ]
    if speaker in own or not own:
        return speaker
    return sorted(own)[0]


class Dial:
    """One speaker's dial: armed by its button, read from its volume."""

    def __init__(self, hass: HomeAssistant, speaker: str, button: str) -> None:
        self._hass = hass
        self._speaker = speaker
        self._button = button
        self._volume_player = volume_player_for(hass, button, speaker)
        self._armed_until = 0.0
        self._home: float | None = None  # the volume to keep, as it was when armed
        self._putting_back = False
        self._clicks = 0
        self._rest: CALLBACK_TYPE | None = None
        self._unwatch = async_track_state_change_event(
            hass, list({button, self._volume_player}), self._changed
        )

    def stop(self) -> None:
        """Stop watching, for good."""
        self._unwatch()
        if self._rest is not None:
            self._rest()
            self._rest = None

    @property
    def armed(self) -> bool:
        return time.monotonic() < self._armed_until

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if old is None or new is None or old.state in _GONE or new.state in _GONE:
            return  # appearing, or coming back after a restart, is not a press or a click
        if event.data["entity_id"] == self._button:
            if new.state != old.state and new.attributes.get("event_type") == ARM_PRESS:
                self._arm()
        elif self.armed and self._home is not None:
            self._volume_changed(old, new)

    def _volume_changed(self, old: State, new: State) -> None:
        before, now = old.attributes.get("volume_level"), new.attributes.get("volume_level")
        if not isinstance(before, (int, float)) or not isinstance(now, (int, float)):
            return
        if now == before or self._home is None:
            return
        if self._putting_back and abs(now - self._home) <= STEP_SLACK:
            self._putting_back = False  # our own change arriving, not a click
            return
        clicks = clicks_in(now - before)
        if new.context.user_id is not None or new.context.parent_id is not None or not clicks:
            # Somebody set the volume, by voice, a dashboard or an automation.
            self._home = float(now)
            self._putting_back = False
            return
        self._clicks += clicks
        self._armed_until = time.monotonic() + ARMED_S
        self._put_back()
        if self._rest is not None:
            self._rest()
        self._rest = async_call_later(self._hass, REST_S, self._rested)

    def _arm(self) -> None:
        state = self._hass.states.get(self._volume_player)
        volume = state.attributes.get("volume_level") if state is not None else None
        if not isinstance(volume, (int, float)):
            return  # no volume to read, so no clicks to see
        if not self.armed:
            self._home = float(volume)
            self._clicks = 0
            self._putting_back = False
        self._armed_until = time.monotonic() + ARMED_S

    def _put_back(self) -> None:
        self._putting_back = True
        self._hass.async_create_task(
            self._set_volume(), f"static_radio dial volume {self._speaker}"
        )

    async def _set_volume(self) -> None:
        try:
            await self._hass.services.async_call(
                "media_player",
                "volume_set",
                {ATTR_ENTITY_ID: self._speaker, "volume_level": self._home},
                blocking=True,
            )
        except HomeAssistantError as err:
            self._putting_back = False
            _LOGGER.warning("Could not put the volume of %s back: %s", self._speaker, err)

    @callback
    def _rested(self, _now: object) -> None:
        self._rest = None
        clicks, self._clicks = self._clicks, 0
        if clicks:
            self._hass.async_create_task(
                self._tune(clicks), f"static_radio dial tune {self._speaker}"
            )

    async def _tune(self, clicks: int) -> None:
        """Tune `clicks` stations along, stopping at the end of the dial."""
        try:
            stations = loaded_entry(self._hass).runtime_data.data.stations
            current = tuned_station(self._hass, self._speaker)
            if not stations:
                return
            if current is None:
                target = stations[0]
            else:
                index = next(
                    (i for i, s in enumerate(stations) if s.station_id == current.station_id), 0
                )
                target = stations[max(0, min(len(stations) - 1, index + clicks))]
                if target.station_id == current.station_id:
                    return  # already at that end of the dial
            await async_tune(self._hass, self._speaker, name=target.display_name, say=True)
        except TuneError as err:
            _LOGGER.debug("dial: %s", err)
        except HomeAssistantError as err:
            _LOGGER.warning("The dial could not tune %s: %s", self._speaker, err)


@callback
def async_setup_dials(hass: HomeAssistant, entry: StaticConfigEntry) -> None:
    """Watch the dial of every speaker that was given a button on the Configure screen."""
    speakers: list[str] = entry.options.get(CONF_SPEAKERS, [])
    buttons: dict[str, str] = entry.options.get(CONF_DIALS, {})
    for speaker in speakers:
        button = buttons.get(speaker)
        if button:
            entry.async_on_unload(Dial(hass, speaker, button).stop)
