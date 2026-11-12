"""Static's stations in Home Assistant's media browser.

Opening "Static Radio (unofficial)" in the media browser lists the stations in
dial order, by name. Opening a station shows what is on it right now: the show
and episode, with the episode's art, and beneath it the address of the
episode's page and, for a show that has one, of its Support page.

Static asks that wherever now-playing is shown, the episode link and the
show's Support link are shown with it. A media browser entry has a title and
a picture but cannot be a link, so the two addresses are written out as
entries of their own, labeled "Episode page" and "Support", right under the
show they belong to. The list of stations itself says nothing about what is
on, so now-playing never appears without them. A bulletin has no Support
entry. The now-playing sensors carry the same two links in a form a dashboard
can make clickable.

There is no episode list: Static is live radio, with no choosing, seeking or
skipping. And the list is for looking, for now. A station cannot be played
from here until the station stream exists, so nothing is marked playable and
resolving anything is refused. No audio address ever leaves this file.
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
from .feed import support_links
from .schedule import Episode, Station


async def async_get_media_source(hass: HomeAssistant) -> StaticMediaSource:
    """Home Assistant asks for this when the media browser is first opened."""
    return StaticMediaSource(hass)


class StaticMediaSource(MediaSource):
    """The stations by name, and inside each, what is on with its links."""

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
        """The list of stations, or one station with what is on and its links."""
        coordinator = self._coordinator()
        if item.identifier:
            return await self._station(coordinator, item.identifier)
        stations = coordinator.data.stations if coordinator is not None else ()
        children = [
            _station_entry(station)
            for station in stations
            # Left out while it must not be played: another day's schedule.
            if coordinator is not None and coordinator.live(station.station_id) is not None
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

    async def _station(
        self, coordinator: StaticCoordinator | None, identifier: str
    ) -> BrowseMediaSource:
        """One station opened: what is on, with its links listed beneath."""
        station_id = identifier.split("/", 1)[0]
        station = coordinator.data.station(station_id) if coordinator is not None else None
        found = coordinator.live(station_id) if coordinator is not None else None
        if coordinator is None or station is None or found is None or identifier != station_id:
            raise BrowseError("That Static Radio station is not on the dial right now")
        episode = found.episode
        links = await support_links(self.hass)
        support_url = await links.for_episode(episode, coordinator.schedule_has_links)

        children = []
        if episode.episode_url.lower().startswith(_WEB):
            children.append(_link_entry(station, "episode", "Episode page", episode.episode_url))
        if support_url:
            children.append(_link_entry(station, "support", "Support", support_url))
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=station.station_id,
            media_class=MediaClass.CHANNEL,
            media_content_type=MediaType.CHANNEL,
            title=_now_playing(station, episode),
            can_play=False,
            can_expand=True,
            # Art that is not on the web is built into Static's own apps: no picture then.
            thumbnail=episode.image_url if episode.image_url.startswith("https://") else None,
            children_media_class=MediaClass.URL,
            children=children,
        )


_WEB = ("https://", "http://")


def _now_playing(station: Station, episode: Episode) -> str:
    if episode.is_bulletin:
        return f"{station.display_name} · Community bulletin"
    return f"{station.display_name} · {episode.feed_title}: {episode.title}"


def _station_entry(station: Station) -> BrowseMediaSource:
    """A station in the list: its name only, and it opens."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=station.station_id,
        media_class=MediaClass.CHANNEL,
        media_content_type=MediaType.CHANNEL,
        title=station.display_name,
        can_play=False,
        can_expand=True,
    )


def _link_entry(station: Station, kind: str, label: str, url: str) -> BrowseMediaSource:
    """A web address written out under a show. It cannot be played or opened."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=f"{station.station_id}/{kind}",
        media_class=MediaClass.URL,
        media_content_type=MediaType.URL,
        title=f"{label}: {url}",
        can_play=False,
        can_expand=False,
    )
