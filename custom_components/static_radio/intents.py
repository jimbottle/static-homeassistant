"""What a voice assistant can answer about Static: what is on, and the Support link.

Three intents, none touching audio:

- StaticWhatsOn says the station, the show and the episode, and mentions that
  the show has a Support link when it does.
- StaticSupportLink gives the show's Support link: spoken in a form that can
  be said aloud, and attached in full for an assistant that shows text.

- StaticSendSupportLink sends that link to the phone chosen on the Configure
  screen, as a notification that opens the show's Support page, and says so.
  With no phone chosen it reads the link out instead.

All also return the episode's page and the Support link as whole addresses in
the response's slots (episode_url, support_url). That is the part of a reply
an assistant with a language model receives, so it can pass on the real link
and not the shortened one that is spoken.

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

from .const import CONF_NOTIFY_SERVICE, DOMAIN
from .coordinator import StaticCoordinator
from .feed import support_links
from .schedule import Episode, Station
from .tuner import find_station, unknown_station_reply

INTENT_WHATS_ON = "StaticWhatsOn"
INTENT_SUPPORT_LINK = "StaticSupportLink"
INTENT_SEND_SUPPORT_LINK = "StaticSendSupportLink"

SLOT_STATION = "station"
_SCHEMES = ("https://", "http://")


@callback
def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the intents, once, when the integration is first loaded."""
    intent.async_register(hass, WhatsOnHandler())
    intent.async_register(hass, SupportLinkHandler())
    intent.async_register(hass, SendSupportLinkHandler())


def spoken_link(url: str) -> str:
    """A web address as someone would read it out: no scheme, no www, no trailing slash."""
    for scheme in _SCHEMES:
        if url.lower().startswith(scheme):
            url = url[len(scheme) :]
            break
    if url.lower().startswith("www."):
        url = url[4:]
    return url.rstrip("/")


def _web(url: str) -> str | None:
    """The address if it is a web address, else None."""
    return url if url.lower().startswith(_SCHEMES) else None


def _plain(text: str) -> str:
    """A title with the punctuation at its end removed, so the reply can end in a period."""
    return text.strip().rstrip(".?!:;, ")


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

    def _reply(
        self, intent_obj: intent.Intent, sentence: str, on_air: _OnAir | None = None
    ) -> intent.IntentResponse:
        """One spoken sentence, with the show's links attached when there is a show."""
        response: intent.IntentResponse = intent_obj.create_response()
        response.async_set_speech(sentence)
        if on_air is not None:
            links = {
                "episode_url": _web(on_air.episode.episode_url),
                "support_url": on_air.support_url,
            }
            # Home Assistant keeps these slots in the result a language model
            # is given, and drops the card.
            response.async_set_speech_slots({k: v for k, v in links.items() if v})
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
            # Only Static's own names are stations; say what they are.
            return unknown_station_reply(coordinator.data.stations)
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
        "episode. Give the station's name, such as News, Nature or Sleep. The result's "
        "speech_slots hold episode_url, the episode's page, and support_url, the show's "
        "Support link, when there is one; show them together, the second labeled Support."
    )

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        on_air = await self._on_air(intent_obj)
        if isinstance(on_air, str):
            return self._reply(intent_obj, on_air)
        name = on_air.station.display_name
        episode = on_air.episode
        if episode.is_bulletin:
            return self._reply(
                intent_obj, f"Static {name} is playing a community bulletin.", on_air
            )
        sentence = f"Static {name} is playing {_plain(episode.feed_title)}"
        if title := _plain(episode.title):
            sentence += f", {title}"
        if on_air.support_url:
            sentence += ", and the show has a Support link"
        return self._reply(intent_obj, sentence + ".", on_air)


class SupportLinkHandler(_StaticHandler):
    """Gives the Support link of the show on a station."""

    intent_type = INTENT_SUPPORT_LINK
    description = (
        "Gives the Support link for the show playing right now on a Static Radio "
        "station: the page where a listener can support the people who make the show. "
        "Give the station's name. The whole link is support_url in the result's "
        "speech_slots; pass that on exactly, labeled Support."
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
                on_air,
            )
        show = _plain(on_air.episode.feed_title)
        if on_air.support_url is None:
            return self._reply(intent_obj, f"{show} has no Support link.", on_air)
        response = self._reply(
            intent_obj, f"You can support {show} at {spoken_link(on_air.support_url)}.", on_air
        )
        # The whole address, for an assistant that shows text or passes it on.
        response.async_set_card("Support", on_air.support_url)
        return response


class SendSupportLinkHandler(_StaticHandler):
    """Sends the Support link of the show on a station to the listener's phone."""

    intent_type = INTENT_SEND_SUPPORT_LINK
    description = (
        "Sends the Support link for the show playing right now on a Static Radio "
        "station to the listener's phone as a notification. Give the station's name. "
        "Use this only when the listener asks for the link to be sent."
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
                on_air,
            )
        show = _plain(on_air.episode.feed_title)
        link = on_air.support_url
        if link is None:
            return self._reply(intent_obj, f"{show} has no Support link.", on_air)

        hass = intent_obj.hass
        entry = hass.config_entries.async_loaded_entries(DOMAIN)[0]
        phone = entry.options.get(CONF_NOTIFY_SERVICE)
        if not phone or not hass.services.has_service("notify", phone):
            return self._reply(
                intent_obj,
                f"No phone is set up for Static links, but you can support {show} "
                f"at {spoken_link(link)}.",
                on_air,
            )
        await hass.services.async_call(
            "notify",
            phone,
            {
                "title": f"Support {show}",
                "message": f"Support: {link}",
                # Tapping the notification opens the show's Support page, on
                # Android (clickAction) and on iOS (url).
                "data": {"clickAction": link, "url": link},
            },
            blocking=True,
        )
        return self._reply(intent_obj, f"I sent the Support link for {show} to your phone.", on_air)
