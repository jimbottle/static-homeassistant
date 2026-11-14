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
skipping. A station can be played from here on a speaker: what is handed
over is the address of that speaker's own stream of the station (stream.py),
live, the same one every other way of tuning uses. It is never an episode's
address, and nothing plays in the browser itself, which is not a speaker
and could be anywhere.
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
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, TITLE
from .coordinator import StaticCoordinator
from .feed import support_links
from .play import stream_url
from .schedule import Episode, Station
from .tune import SELECTORS_KEY


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
        """The address of a station's stream for the speaker it is to play on."""
        entries = self.hass.config_entries.async_loaded_entries(DOMAIN)
        coordinator = self._coordinator()
        station = coordinator.data.station(item.identifier) if coordinator is not None else None
        if not entries or coordinator is None or station is None:
            raise Unresolvable("That is not a Static Radio station")
        if coordinator.live(station.station_id) is None:
            raise Unresolvable("Static's schedule is not available right now")
        speaker = item.target_media_player
        if not speaker:
            raise Unresolvable("Choose a speaker to play Static Radio on")
        try:
            url = stream_url(self.hass, entries[0], speaker, station, say=True)
        except HomeAssistantError as err:
            raise Unresolvable(str(err)) from err
        # A speaker that has a selector shows this station on it from now on.
        selector = self.hass.data.get(SELECTORS_KEY, {}).get(speaker)
        if selector is not None:
            selector.note(station)
        return PlayMedia(url, "audio/flac")

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
            can_play=True,
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
    """A station in the list: its name only. It plays, and it opens."""
    return BrowseMediaSource(
        domain=DOMAIN,
        identifier=station.station_id,
        media_class=MediaClass.CHANNEL,
        media_content_type=MediaType.CHANNEL,
        title=station.display_name,
        can_play=True,
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
