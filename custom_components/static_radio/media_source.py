"""Static's stations in Home Assistant's media browser.

Opening "Static Radio (unofficial)" in the media browser lists the stations in
dial order, each with the show and episode that are on right now and the
episode's cover art. There is nothing below a station: no episode list, since
Static is live radio and offers no choosing, seeking or skipping.

The list is for looking, for now. A station cannot be played from here until
the station stream exists, so each entry is marked as not playable and
resolving one is refused. No audio address ever leaves this file.
"""

from __future__ import annotations

from homeassistant.components.media_player.const import MediaClass, MediaType
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN, TITLE
from .coordinator import StaticCoordinator
from .schedule import Station


async def async_get_media_source(hass: HomeAssistant) -> StaticMediaSource:
    """Home Assistant asks for this when the media browser is first opened."""
    return StaticMediaSource(hass)


class StaticMediaSource(MediaSource):
    """The stations, each with what is on now."""

    name = TITLE

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _coordinator(self) -> StaticCoordinator | None:
        """The loaded entry's coordinator, or None while there is none."""
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator: StaticCoordinator = entry.runtime_data
            return coordinator
        return None

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Refused: playing a station waits for the station stream."""
        raise Unresolvable("Static Radio stations cannot be played from the media browser yet")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """The list of stations. A station itself has nothing inside it."""
        if item.identifier:
            raise BrowseError("A Static Radio station is live and has nothing to browse")
        coordinator = self._coordinator()
        stations = coordinator.data.stations if coordinator is not None else ()
        children = [
            child
            for station in stations
            if coordinator is not None and (child := _station(coordinator, station)) is not None
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.CHANNELS,
            title=TITLE,
            can_play=False,
            can_expand=True,
            children_media_class=MediaClass.CHANNEL,
            children=children,
        )


def _station(coordinator: StaticCoordinator, station: Station) -> BrowseMediaSource | None:
    """One station's entry, or None while it must not be played (another day's schedule)."""
    found = coordinator.live(station.station_id)
    if found is None:
        return None
    episode = found.episode
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=station.station_id,
        media_class=MediaClass.CHANNEL,
        media_content_type=MediaType.CHANNEL,
        title=f"{station.display_name} · {episode.feed_title}: {episode.title}",
        can_play=False,
        can_expand=False,
        # Art that is not on the web is built into Static's own apps: no picture then.
        thumbnail=episode.image_url if episode.image_url.startswith("https://") else None,
    )
