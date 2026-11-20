"""Tuning with a speaker's own volume dial, on firmware that was not changed.

A Voice Preview Edition on stock firmware tells Home Assistant nothing about
its dial but this: each click moves the volume of the device's own media
player by one step of 0.05, and a double press of its button fires a "Button
press" event. This file turns those two facts into a tuning dial:

- A double press arms the dial for ARMED_S. Until then, and after, the dial is
  the volume control it always was.
- While armed, each volume step is taken as one click along Static's dial
  and the volume is put back at once. Each click keeps the dial armed for
  another ARMED_S.
- Turning is for finding a station, so each click is answered at once: the
  device says the name of the station the dial is now on, through its own
  announcements, about half a second after the click (measured). Only when
  the dial has rested for TUNE_REST_S is that station loaded, so the stations
  passed on the way are never fetched from their shows' hosts. A listener
  hearing each name out clicks every 0.7 to 1.5 s (measured), so the rest has
  to be longer than that.
- A device with no announcements of its own cannot say the name early. Its
  station is loaded after REST_S, and the stream opens with the name.
- Static's dial is a circle, so a turn on from the last station comes round
  to the first, and a turn back from the first to the last. A turn that
  comes all the way round to where it began says that station's name again.

Measured on the reference speaker (firmware 26.9.0): one slow click is one
update of 0.05, and fast clicks arrive as one update of several steps, so
steps are counted from the size of the change and never from the number of
updates. In a fast turn a click and the volume being put back arrive as one
change; a change towards the kept volume while it is being put back is read
as "put back, then clicks". What the dial cannot do this way: turn past
volume 0 or 1 (the device reports nothing there), and count a click the other
way that crosses the volume being put back.

The volume kept is the one the speaker had when the dial was armed. It moves
only when somebody sets the volume, or on a change too big to be drift: the
reference speaker's volume crept by 0.01 around announcements, and following
that walked the volume up. A change that lands exactly on 0 or 1 is a click
against the end of the range, however small, and never a volume to keep:
keeping 0 left the reference speaker muted.

The volume is put back through the speaker chosen on the Configure screen and
not through the device's own player, which a home may keep hands off.

A speaker flashed with the custom firmware overlay in esphome/ needs none of
the above. Its "Button press" entity reports each click as a dial_clockwise
or dial_anticlockwise event, and the volume is never touched. For such a
speaker this file:

- takes each of those events as one click, with no double press first;
- turns the device's "Static dial" switch on while Static plays on the
  speaker and off when it stops, so that the dial is the volume dial for
  everything else;
- keeps the device's "Static dial station" number at the dial's place, which
  the firmware shows as one lit LED of its ring while the dial is turned.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
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
from homeassistant.util import dt as dt_util

from .const import CONF_DIALS, CONF_SPEAKERS, DOMAIN
from .schedule import Station
from .tune import TuneError, async_tune, loaded_entry, selector_unique_id, tuned_station

if TYPE_CHECKING:
    from . import StaticConfigEntry

_LOGGER = logging.getLogger(__name__)

ARMED_S = 8.0  # how long the dial tunes after a double press, and after each click
REST_S = 0.3  # with no early name: the dial has stopped when no click has come for this long
SAY_REST_S = 0.12  # a name is said once no click has come for this long, so a flick says one
TUNE_REST_S = 2.0  # with the name said early: load the station after this long at rest
PRESS_FRESH_S = 10.0  # a press older than this is one remembered from before a restart
STEP = 0.05  # the volume change of one click, of a full range of 1
STEP_SLACK = 0.006  # a player that keeps whole percents rounds a step by up to this
DRIFT = 0.02  # a change smaller than this that is not a step is the device, not a person
ARM_PRESS = "double_press"  # the button event that arms the dial
# The custom firmware's events for one click of the dial, and which way it went.
FIRMWARE_CLICKS = {"dial_clockwise": 1, "dial_anticlockwise": -1}
FIRMWARE_SWITCH = "static_dial"  # the end of the unique id of the firmware's switch
FIRMWARE_PLACE = "static_dial_station"  # and of the number its LED ring shows

_GONE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


def clicks_in(change: float) -> int:
    """How many dial clicks a change of volume is. 0 when it is not a whole number of steps."""
    clicks = round(change / STEP)
    return clicks if abs(change - clicks * STEP) <= STEP_SLACK else 0


def _on_device(hass: HomeAssistant, button: str, domain: str) -> list[str]:
    """The entities of one kind on the device the button belongs to."""
    entities = er.async_get(hass)
    known = entities.async_get(button)
    if known is None or known.device_id is None:
        return []
    return sorted(
        e.entity_id
        for e in er.async_entries_for_device(entities, known.device_id)
        if e.domain == domain
    )


def _firmware_entity(hass: HomeAssistant, button: str, domain: str, key: str) -> str | None:
    """An entity the custom firmware adds to the button's device, if it runs there."""
    entities = er.async_get(hass)
    for entity_id in _on_device(hass, button, domain):
        known = entities.async_get(entity_id)
        if known is not None and known.unique_id.endswith(key):
            return entity_id
    return None


def volume_player_for(hass: HomeAssistant, button: str, speaker: str) -> str:
    """The media player whose volume the dial moves: the one on the button's own device.

    Falls back to the speaker itself, which is right for a speaker tuned
    through its own player.
    """
    own = _on_device(hass, button, "media_player")
    if speaker in own or not own:
        return speaker
    return own[0]


def announcer_for(hass: HomeAssistant, button: str) -> str | None:
    """What says a name on the button's device at once: its voice satellite, if it has one."""
    own = _on_device(hass, button, "assist_satellite")
    return own[0] if own else None


def _number(text: str) -> int | None:
    try:
        return round(float(text))
    except ValueError:
        return None


class Dial:
    """One speaker's dial: armed by its button, read from its volume."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: StaticConfigEntry,
        speaker: str,
        button: str,
        selector: str | None,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._speaker = speaker
        self._button = button
        self._volume_player = volume_player_for(hass, button, speaker)
        self._announcer = announcer_for(hass, button)
        self._armed_until = 0.0
        self._home: float | None = None  # the volume to keep, as it was when armed
        self._putting_back = False
        self._target: Station | None = None  # the station the dial is on, while armed
        self._said: str | None = None  # the station id last said aloud
        self._saying = False
        self._say_rest: CALLBACK_TYPE | None = None
        self._tune_rest: CALLBACK_TYPE | None = None
        self._clicked_until = 0.0  # custom firmware: the dial keeps its place until then
        self._switch = _firmware_entity(hass, button, "switch", FIRMWARE_SWITCH)
        self._place = _firmware_entity(hass, button, "number", FIRMWARE_PLACE)
        self._unwatch = [
            async_track_state_change_event(hass, list({button, self._volume_player}), self._changed)
        ]
        if self._switch is not None or self._place is not None:
            watched = [e for e in (selector, self._switch, self._place) if e is not None]
            self._selector = selector
            self._unwatch.append(
                async_track_state_change_event(hass, watched, self._firmware_changed)
            )
            self._follow()

    def stop(self) -> None:
        """Stop watching, for good."""
        for unwatch in self._unwatch:
            unwatch()
        self._cancel_rests()

    def _cancel_rests(self) -> None:
        for waiting in (self._say_rest, self._tune_rest):
            if waiting is not None:
                waiting()
        self._say_rest = self._tune_rest = None

    @property
    def armed(self) -> bool:
        return time.monotonic() < self._armed_until

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or new.state in _GONE:
            return
        if event.data["entity_id"] == self._button:
            if (old is None or new.state != old.state) and self._pressed_just_now(new):
                kind = new.attributes.get("event_type")
                if kind in FIRMWARE_CLICKS:
                    self._clicked(FIRMWARE_CLICKS[kind])
                elif kind == ARM_PRESS and self._switch is None:
                    self._arm()  # custom firmware needs no arming: its turns are clicks
        elif old is not None and self.armed and self._home is not None:
            self._volume_changed(old, new)

    @staticmethod
    def _pressed_just_now(press: State) -> bool:
        """A press or click made now, and not the last one remembered across a restart."""
        try:
            at = datetime.fromisoformat(press.state)
        except ValueError:
            return False
        return abs(dt_util.utcnow() - dt_util.as_utc(at)) <= timedelta(seconds=PRESS_FRESH_S)

    def _volume_changed(self, old: State, new: State) -> None:
        before, now = old.attributes.get("volume_level"), new.attributes.get("volume_level")
        if not isinstance(before, (int, float)) or not isinstance(now, (int, float)):
            return
        if now == before or self._home is None:
            return
        change = now - before
        if self._putting_back and (self._home - before) * change > 0:
            # Moving towards the kept volume while it is being put back: it
            # was put back, and whatever is left over is clicks made since.
            self._putting_back = False
            change = now - self._home
            if abs(change) <= STEP_SLACK:
                return
        clicks = clicks_in(change)
        by_someone = new.context.user_id is not None or new.context.parent_id is not None
        at_an_end = now <= STEP_SLACK or now >= 1 - STEP_SLACK  # the device reports 7e-9 for 0
        if not by_someone and not clicks and at_an_end:
            clicks = 1 if change > 0 else -1  # a click cut short by the end of the range
        if by_someone or not clicks:
            if by_someone or abs(change) >= DRIFT:
                # Somebody set the volume, by voice, a dashboard or an automation.
                self._home = float(now)
                self._putting_back = False
            return
        self._armed_until = time.monotonic() + ARMED_S
        self._put_back()
        self._turn(clicks)

    def _arm(self) -> None:
        state = self._hass.states.get(self._volume_player)
        volume = state.attributes.get("volume_level") if state is not None else None
        if not isinstance(volume, (int, float)):
            return  # no volume to read, so no clicks to see
        if not self.armed:
            self._home = float(volume)
            self._putting_back = False
            self._target = None  # the dial starts from the station the speaker is on
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

    # -- a speaker on the custom firmware ------------------------------------

    def _clicked(self, clicks: int) -> None:
        """One click reported by the custom firmware. The volume was not touched."""
        now = time.monotonic()
        if now >= self._clicked_until:
            self._target = None  # the dial starts from the station the speaker is on
        self._clicked_until = now + ARMED_S
        self._turn(clicks)

    @callback
    def _firmware_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or new.state in _GONE:
            return
        if event.data["entity_id"] != self._selector and not (old is None or old.state in _GONE):
            return  # a switch somebody set by hand is left as they set it
        self._follow()

    def _follow(self) -> None:
        """Have the device tune by its dial while Static plays, and show the right place."""
        selector = self._hass.states.get(self._selector) if self._selector else None
        if selector is None:
            return
        playing = bool(selector.attributes.get("playing_static"))
        self._ask(self._switch, "switch", "turn_on" if playing else "turn_off", {}, playing)
        if time.monotonic() >= self._clicked_until:
            try:
                # Not loaded_entry: this also runs while the integration is still loading.
                stations = self._entry.runtime_data.data.stations
                current = tuned_station(self._hass, self._speaker)
            except TuneError:
                return
            self._show(stations, current)

    def _show(self, stations: Sequence[Station], station: Station | None) -> None:
        """Tell the device where the dial is: 1 for the first station, 0 for nowhere."""
        place = 0
        if station is not None:
            ids = [st.station_id for st in stations]
            place = ids.index(station.station_id) + 1 if station.station_id in ids else 0
        self._ask(self._place, "number", "set_value", {"value": place}, place)

    def _ask(
        self, entity_id: str | None, domain: str, action: str, data: dict[str, object], want: object
    ) -> None:
        """Ask the device for a state it is not already in. Nothing is asked of one that is away."""
        state = self._hass.states.get(entity_id) if entity_id is not None else None
        if state is None or state.state in _GONE:
            return
        now: object = state.state == "on" if domain == "switch" else _number(state.state)
        if now == want:
            return
        self._hass.async_create_task(
            self._call(domain, action, {ATTR_ENTITY_ID: entity_id, **data}),
            f"static_radio dial {action} {self._speaker}",
        )

    async def _call(self, domain: str, action: str, data: dict[str, object]) -> None:
        try:
            await self._hass.services.async_call(domain, action, data, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("The dial of %s could not %s: %s", self._speaker, action, err)

    # -- where the dial is ----------------------------------------------------

    def _turn(self, clicks: int) -> None:
        """Move the dial `clicks` stations along, round the circle."""
        try:
            stations = loaded_entry(self._hass).runtime_data.data.stations
            current = tuned_station(self._hass, self._speaker)
        except TuneError as err:
            _LOGGER.debug("dial: %s", err)
            return
        if not stations:
            return
        on = self._target
        if on is None:
            # The first click since the dial was armed starts from the speaker's
            # station. Later ones start from where the dial is, which a station
            # still being loaded has not caught up with.
            self._said = current.station_id if current is not None else None
            on, clicks = (current, clicks) if current is not None else (stations[0], 0)
        index = next((i for i, st in enumerate(stations) if st.station_id == on.station_id), 0)
        self._target = stations[(index + clicks) % len(stations)]
        if clicks and self._target.station_id == on.station_id:
            self._said = None  # all the way round: say where it is again
        self._show(stations, self._target)
        self._cancel_rests()
        if self._announcer is None:
            self._tune_rest = async_call_later(self._hass, REST_S, self._rested)
        else:
            self._say_rest = async_call_later(self._hass, SAY_REST_S, self._say_now)
            self._tune_rest = async_call_later(self._hass, TUNE_REST_S, self._rested)

    @callback
    def _say_now(self, _now: object) -> None:
        self._say_rest = None
        if not self._saying:
            self._hass.async_create_task(self._say(), f"static_radio dial name {self._speaker}")

    async def _say(self) -> None:
        """Say the station the dial is on, and again if it has moved on meanwhile."""
        self._saying = True
        try:
            while (target := self._target) is not None and target.station_id != self._said:
                self._said = target.station_id
                await self._hass.services.async_call(
                    "assist_satellite",
                    "announce",
                    {
                        ATTR_ENTITY_ID: self._announcer,
                        "message": target.display_name,
                        "preannounce": False,
                    },
                    blocking=True,
                )
        except HomeAssistantError as err:
            _LOGGER.warning("The dial could not say a station on %s: %s", self._speaker, err)
            self._announcer = None  # the stream says the name from now on
        finally:
            self._saying = False

    @callback
    def _rested(self, _now: object) -> None:
        self._tune_rest = None
        target = self._target
        if target is not None:
            self._hass.async_create_task(
                self._tune(target), f"static_radio dial tune {self._speaker}"
            )

    async def _tune(self, target: Station) -> None:
        try:
            current = tuned_station(self._hass, self._speaker)
            if current is not None and current.station_id == target.station_id:
                return  # back where it started, or already at that end of the dial
            await async_tune(
                self._hass,
                self._speaker,
                name=target.display_name,
                # The name has been said already when the device says names itself.
                say=self._announcer is None,
            )
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
            # Found by its unique id: the selector itself may not have loaded yet.
            selector = er.async_get(hass).async_get_entity_id(
                "select", DOMAIN, selector_unique_id(entry.entry_id, speaker)
            )
            entry.async_on_unload(Dial(hass, entry, speaker, button, selector).stop)
