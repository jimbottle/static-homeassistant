"""Tuning a speaker to a station: the one path every way of asking takes.

A dashboard's selector, the static_radio.tune action, a voice request and a
dial all end here. This file works out which speaker and which station are
meant, has the speaker's selector (select.py) start the stream and remember
the choice, and gives back the station so the caller can name it.

Which station:

- A name means that station. Only Static's own names are stations (tuner.py).
- A step means that many places along the dial from the speaker's station.
  The dial has two ends and does not wrap.
- Neither means the station this speaker was last tuned to, or the first on
  the dial for a speaker that has never been tuned.

Which speaker, when none is named: the only one set up; else the one that is
the device spoken to; else the one in the same room as it.

When a request cannot be met, TuneError carries the one sentence to say.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_SPEAKERS, DOMAIN
from .schedule import Station
from .tuner import end_of_dial_reply, find_station, step, unknown_station_reply

if TYPE_CHECKING:
    from . import StaticConfigEntry
    from .select import StationSelect

SERVICE_TUNE = "tune"
ATTR_SPEAKER = "entity_id"
ATTR_STATION = "station"
ATTR_STEP = "step"

SELECTORS_KEY = f"{DOMAIN}.selectors"  # speaker entity id -> its station selector

NOT_SET_UP = "Static Radio is not set up."
NO_SPEAKER = "No speaker is set up to play Static."
WHICH_SPEAKER = "More than one speaker can play Static, so say which one."
NO_STATIONS = "Static has no stations on the dial right now."

_TUNE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SPEAKER): cv.entity_id,
        vol.Exclusive(ATTR_STATION, "which"): cv.string,
        vol.Exclusive(ATTR_STEP, "which"): vol.All(vol.Coerce(int), vol.Range(min=-20, max=20)),
    }
)


class TuneError(HomeAssistantError):
    """A request to tune that cannot be met. Its text is the sentence to say."""


def loaded_entry(hass: HomeAssistant) -> StaticConfigEntry:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise TuneError(NOT_SET_UP)
    entry: StaticConfigEntry = entries[0]
    return entry


def _area(hass: HomeAssistant, entity_id: str) -> str | None:
    """The room an entity is in: its own, or its device's."""
    known = er.async_get(hass).async_get(entity_id)
    if known is None:
        return None
    if known.area_id:
        return known.area_id
    device = dr.async_get(hass).async_get(known.device_id) if known.device_id else None
    return device.area_id if device is not None else None


def speaker_for(hass: HomeAssistant, device_id: str | None = None) -> str:
    """The speaker a request with no speaker named is about.

    device_id is the device that was spoken to, when there was one.
    """
    speakers: list[str] = list(loaded_entry(hass).options.get(CONF_SPEAKERS, []))
    if not speakers:
        raise TuneError(NO_SPEAKER)
    if len(speakers) == 1:
        return speakers[0]
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    if device is not None:
        entities = er.async_get(hass)
        own = [s for s in speakers if (e := entities.async_get(s)) and e.device_id == device.id]
        if len(own) == 1:
            return own[0]
        if device.area_id:
            near = [s for s in speakers if _area(hass, s) == device.area_id]
            if len(near) == 1:
                return near[0]
    raise TuneError(WHICH_SPEAKER)


def selector_for(hass: HomeAssistant, speaker: str) -> StationSelect:
    selectors: dict[str, StationSelect] = hass.data.get(SELECTORS_KEY, {})
    if speaker not in selectors:
        raise TuneError(NO_SPEAKER)
    return selectors[speaker]


def tuned_station(hass: HomeAssistant, speaker: str) -> Station | None:
    """The station a speaker was last tuned to, if it is still on the dial."""
    return selector_for(hass, speaker).station


def pick_station(
    stations: tuple[Station, ...], current: Station | None, name: str | None, by: int
) -> Station:
    """The station a request means, by the rules at the top of this file."""
    if not stations:
        raise TuneError(NO_STATIONS)
    if name:
        named = find_station(stations, name)
        if named is None:
            raise TuneError(unknown_station_reply(stations))
        return named
    if by:
        stepped = step(stations, current.station_id if current else None, by)
        if stepped is None:
            # Only reached with a current station: with none, a step lands on the first.
            assert current is not None  # noqa: S101
            raise TuneError(end_of_dial_reply(current, by))
        return stepped
    return current or stations[0]


async def async_tune(
    hass: HomeAssistant, speaker: str, *, name: str | None = None, by: int = 0, say: bool
) -> Station:
    """Tune a speaker and give back the station it is now on.

    say: whether the stream may open with the station's name, spoken. A caller
    that names the station itself, as the voice assistant does, passes False.
    """
    entry = loaded_entry(hass)
    selector = selector_for(hass, speaker)
    station = pick_station(entry.runtime_data.data.stations, selector.station, name, by)
    await selector.async_tune(station, say=say)
    return station


@callback
def async_setup_tune(hass: HomeAssistant) -> None:
    """Register the static_radio.tune action, once for the life of Home Assistant."""

    async def tune(call: ServiceCall) -> None:
        try:
            speaker = call.data.get(ATTR_SPEAKER) or speaker_for(hass)
            await async_tune(
                hass,
                speaker,
                name=call.data.get(ATTR_STATION),
                by=call.data.get(ATTR_STEP, 0),
                say=True,
            )
        except TuneError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(DOMAIN, SERVICE_TUNE, tune, schema=_TUNE_SCHEMA)
