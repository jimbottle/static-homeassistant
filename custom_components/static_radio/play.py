"""Starts a station's stream on a speaker.

A speaker is handed one address (stream.py) and plays it as it would any
radio stream. Which action hands it over depends on what the speaker is:

- A Music Assistant player is asked through music_assistant.play_media, so
  Music Assistant's own queue knows what is playing and stays in step.
- Any other media player is asked through media_player.play_media. A speaker
  that is an ESPHome device is also told to fetch the stream itself
  (bypass_proxy), because Home Assistant would otherwise decode and encode it
  a second time on the way.

The address is on Home Assistant's internal URL, never its external one: the
stream answers without sign-in and is meant for the home network only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .schedule import Station
from .stream import stream_path

if TYPE_CHECKING:
    from . import StaticConfigEntry

MUSIC_ASSISTANT = "music_assistant"
ESPHOME = "esphome"


def _platform(hass: HomeAssistant, speaker: str) -> str | None:
    """The integration a speaker belongs to."""
    known = er.async_get(hass).async_get(speaker)
    return known.platform if known is not None else None


def stream_url(
    hass: HomeAssistant, entry: StaticConfigEntry, speaker: str, station: Station, *, say: bool
) -> str:
    """The whole address of a station's stream for one speaker, on the home network."""
    try:
        base = get_url(hass, allow_external=False, allow_cloud=False, prefer_external=False)
    except NoURLAvailableError as err:
        raise HomeAssistantError(
            "Home Assistant has no address on the home network to play Static from"
        ) from err
    return base.rstrip("/") + stream_path(hass, entry, speaker, station.station_id, say=say)


async def async_play(
    hass: HomeAssistant, entry: StaticConfigEntry, speaker: str, station: Station, *, say: bool
) -> None:
    """Hand the speaker the station's stream. say: may the stream open with its name."""
    url = stream_url(hass, entry, speaker, station, say=say)

    platform = _platform(hass, speaker)
    if platform == MUSIC_ASSISTANT and hass.services.has_service(MUSIC_ASSISTANT, "play_media"):
        await hass.services.async_call(
            MUSIC_ASSISTANT,
            "play_media",
            {"entity_id": speaker, "media_id": url, "media_type": "radio"},
            blocking=True,
        )
        return
    data: dict[str, Any] = {
        "entity_id": speaker,
        "media_content_id": url,
        "media_content_type": "music",
    }
    if platform == ESPHOME:
        data["extra"] = {"bypass_proxy": True}
    await hass.services.async_call("media_player", "play_media", data, blocking=True)
