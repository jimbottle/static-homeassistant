"""What a voice assistant can answer about Static: what is on, and the Support link.

Two intents, both read-only and neither touching audio:

- StaticWhatsOn says the station, the show and the episode, and mentions that
  the show has a Support link when it does.
- StaticSupportLink gives the show's Support link: spoken in a form that can
  be said aloud, and attached in full for an assistant that shows text.

Static asks that the Support link be shown wherever now-playing is shown. A
speaker has no screen, so this is how the link reaches a listener who asks,
and how an assistant with a language model gets it to pass on. A community
bulletin never has a Support link.

Every reply is one sentence that ends in a period and never in a question
mark. The sentences that reach these intents are in sentences/en.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .const import DOMAIN
from .coordinator import StaticCoordinator
from .feed import support_links
from .schedule import Episode, Station

INTENT_WHATS_ON = "StaticWhatsOn"
INTENT_SUPPORT_LINK = "StaticSupportLink"

SLOT_STATION = "station"
_SCHEMES = ("https://", "http://")


@callback
def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the intents, once, when the integration is first loaded."""
    intent.async_register(hass, WhatsOnHandler())
    intent.async_register(hass, SupportLinkHandler())


def spoken_link(url: str) -> str:
    """A web address as someone would read it out: no scheme, no www, no trailing slash."""
    for scheme in _SCHEMES:
        if url.lower().startswith(scheme):
            url = url[len(scheme) :]
            break
    if url.lower().startswith("www."):
        url = url[4:]
    return url.rstrip("/")


def _plain(text: str) -> str:
    """A title with the punctuation at its end removed, so the reply can end in a period."""
    return text.strip().rstrip(".?!:;, ")


def find_station(stations: tuple[Station, ...], said: str) -> Station | None:
    """The station a listener or an assistant named: by name, dial tag or id."""
    wanted = said.strip().casefold()
    for station in stations:
        names = (station.display_name, station.u_tag, station.station_id)
        if wanted in (name.casefold() for name in names):
            return station
    return None


@dataclass(frozen=True, slots=True)
class _OnAir:
    """What a question was about, once the station has been worked out."""

    station: Station
    episode: Episode
    support_url: str | None


class _StaticHandler(intent.IntentHandler):
    """Shared by both intents: find the station, then what is live on it."""

    @property
    def slot_schema(self) -> dict[Any, Any]:
        return {vol.Optional(SLOT_STATION): cv.string}

    def _reply(self, intent_obj: intent.Intent, sentence: str) -> intent.IntentResponse:
        response: intent.IntentResponse = intent_obj.create_response()
        response.async_set_speech(sentence)
        return response

    async def _on_air(self, intent_obj: intent.Intent) -> _OnAir | str:
        """What is on the station asked about, or the sentence to say instead."""
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        loaded = hass.config_entries.async_loaded_entries(DOMAIN)
        if not loaded:
            return "Static Radio is not set up."
        coordinator: StaticCoordinator = loaded[0].runtime_data

        said = slots.get(SLOT_STATION, {}).get("value")
        if not said:
            # Nothing is tuned yet in this version, so the station has to be named.
            return "Say which Static station, for example Static News."
        station = find_station(coordinator.data.stations, str(said))
        if station is None:
            return f"Static has no station called {_plain(str(said))}."
        found = coordinator.live(station.station_id)
        if found is None:
            return "Static's schedule is not available right now."

        links = await support_links(hass)
        link = await links.for_episode(found.episode, coordinator.schedule_has_links)
        return _OnAir(station, found.episode, link)


class WhatsOnHandler(_StaticHandler):
    """Says what is on a station."""

    intent_type = INTENT_WHATS_ON
    description = (
        "Says what is playing right now on a Static Radio station: the show and the "
        "episode. Give the station's name, such as News, Nature or Sleep."
    )

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        on_air = await self._on_air(intent_obj)
        if isinstance(on_air, str):
            return self._reply(intent_obj, on_air)
        name = on_air.station.display_name
        episode = on_air.episode
        if episode.is_bulletin:
            return self._reply(intent_obj, f"Static {name} is playing a community bulletin.")
        sentence = f"Static {name} is playing {_plain(episode.feed_title)}"
        if title := _plain(episode.title):
            sentence += f", {title}"
        if on_air.support_url:
            sentence += ", and the show has a Support link"
        return self._reply(intent_obj, sentence + ".")


class SupportLinkHandler(_StaticHandler):
    """Gives the Support link of the show on a station."""

    intent_type = INTENT_SUPPORT_LINK
    description = (
        "Gives the Support link for the show playing right now on a Static Radio "
        "station: the page where a listener can support the people who make the show. "
        "Give the station's name. Pass the link on to the listener exactly as returned."
    )

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        on_air = await self._on_air(intent_obj)
        if isinstance(on_air, str):
            return self._reply(intent_obj, on_air)
        name = on_air.station.display_name
        if on_air.episode.is_bulletin:
            return self._reply(
                intent_obj,
                f"Static {name} is playing a community bulletin, which has no Support link.",
            )
        show = _plain(on_air.episode.feed_title)
        if on_air.support_url is None:
            return self._reply(intent_obj, f"{show} has no Support link.")
        response = self._reply(
            intent_obj, f"You can support {show} at {spoken_link(on_air.support_url)}."
        )
        # The whole address, for an assistant that shows text or passes it on.
        response.async_set_card("Support", on_air.support_url)
        return response
