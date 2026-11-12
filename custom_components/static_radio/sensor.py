"""What is on each station right now, as one sensor per station.

The sensor's state is the show's name. Its attributes carry the episode, the
link to the episode's page and, for a show that has one, the link to its
Support page: Static asks that the two be shown together wherever now-playing
is shown, and that a bulletin never has a Support link. The cover art is the
entity's picture when the schedule gives a web address for it; a few shows'
art is built into Static's own apps, and those fall back to the icon.

Stations are read from the schedule, so a station that joins the dial gets a
sensor and one that leaves goes unavailable. Each sensor changes at the
moment its station moves to the next episode. It touches no audio.

Each station also has a listeners sensor with Static's live count. Those start
disabled: Static is asked for the counts, once a minute, only while at least
one of them has been switched on.
"""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import StaticConfigEntry
from .const import DOMAIN, STATIC_URL, TITLE
from .coordinator import StaticCoordinator
from .counts import CountsCoordinator, counts_client
from .feed import support_links
from .schedule import Episode, Station
from .support import SupportLinks

STATE_MAX_CHARS = 255  # Home Assistant's limit on a state


def web_link(url: str | None) -> str | None:
    """The address if it is one a listener can safely tap, else None."""
    if url and url.lower().startswith(("https://", "http://")):
        return url
    return None


def art(episode: Episode) -> str | None:
    """Cover art, or None to fall back to the icon for art that is not on the web."""
    return episode.image_url if episode.image_url.startswith("https://") else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaticConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a sensor for every station, now and as stations join the dial."""
    coordinator = entry.runtime_data
    links = await support_links(hass)
    counts = CountsCoordinator(hass, entry, await counts_client(hass))
    known: set[str] = set()

    @callback
    def add_new_stations() -> None:
        new = [s for s in coordinator.data.stations if s.station_id not in known]
        known.update(s.station_id for s in new)
        if new:
            async_add_entities(NowPlayingSensor(coordinator, entry, links, s) for s in new)
            async_add_entities(ListenersSensor(counts, coordinator, entry, s) for s in new)

    add_new_stations()
    entry.async_on_unload(coordinator.async_add_listener(add_new_stations))


def service_device(entry: StaticConfigEntry) -> DeviceInfo:
    """The one device every sensor belongs to."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=TITLE,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=STATIC_URL,
    )


class NowPlayingSensor(CoordinatorEntity[StaticCoordinator], SensorEntity):
    """The show on one station, with its episode and its links."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:radio"

    def __init__(
        self,
        coordinator: StaticCoordinator,
        entry: StaticConfigEntry,
        links: SupportLinks,
        station: Station,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._links = links
        self._station_id = station.station_id
        self._episode: Episode | None = None
        self._support_url: str | None = None
        self._lookup: asyncio.Task[None] | None = None
        self._attr_unique_id = f"{entry.entry_id}_{station.station_id}_now_playing"
        self._attr_name = station.display_name
        self._attr_device_info = service_device(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._follow_the_station()

    async def async_will_remove_from_hass(self) -> None:
        self._drop_lookup()
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._follow_the_station()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Unavailable off the dial, and while the schedule is another day's.

        A failed refresh alone does not make it unavailable: the copy in hand
        still says what is on.
        """
        return self._episode is not None

    @property
    def native_value(self) -> str | None:
        if self._episode is None:
            return None
        return self._episode.feed_title[:STATE_MAX_CHARS] or None

    @property
    def entity_picture(self) -> str | None:
        return art(self._episode) if self._episode is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        episode = self._episode
        if episode is None:
            return None
        return {
            "station_id": self._station_id,
            "episode": episode.title,
            "episode_url": web_link(episode.episode_url),
            "support_url": self._support_url,
            "bulletin": episode.is_bulletin,
        }

    # -- keeping up with the station -----------------------------------------

    @callback
    def _follow_the_station(self) -> None:
        """Read what is live; on a new episode, start over on its Support link."""
        found = self.coordinator.live(self._station_id)
        episode = found.episode if found is not None else None
        if episode == self._episode:
            return
        self._drop_lookup()
        self._episode = episode
        # No link carries over from the last show, least of all onto a bulletin.
        self._support_url = None
        if episode is None or episode.is_bulletin:
            return
        self._lookup = self._entry.async_create_background_task(
            self.hass,
            self._find_support_link(episode),
            name=f"{DOMAIN} support link {self._station_id}",
        )

    @callback
    def _drop_lookup(self) -> None:
        if self._lookup is not None:
            self._lookup.cancel()
            self._lookup = None

    async def _find_support_link(self, episode: Episode) -> None:
        link = await self._links.for_episode(episode, self.coordinator.schedule_has_links)
        self._lookup = None
        if link is not None and episode == self._episode:
            self._support_url = link
            self.async_write_ha_state()


class ListenersSensor(CoordinatorEntity[CountsCoordinator], SensorEntity):
    """Static's live listener count for one station. Off until switched on."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-multiple"
    _attr_native_unit_of_measurement = "listeners"
    _attr_state_class = SensorStateClass.MEASUREMENT
    # Enabling one starts a request to Static every minute, so the listener
    # chooses that; nothing is asked for a number nobody looks at.
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        counts: CountsCoordinator,
        schedule: StaticCoordinator,
        entry: StaticConfigEntry,
        station: Station,
    ) -> None:
        super().__init__(counts)
        self._schedule = schedule
        self._station_id = station.station_id
        self._attr_unique_id = f"{entry.entry_id}_{station.station_id}_listeners"
        self._attr_name = f"{station.display_name} listeners"
        self._attr_device_info = service_device(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The first numbers now, not a minute from now. The client's own
        # floor still holds if several sensors are added together.
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """Unavailable once the station has left the dial."""
        on_the_dial = self._schedule.data.station(self._station_id) is not None
        return on_the_dial and self.coordinator.data is not None

    @property
    def native_value(self) -> int | None:
        """The count, or unknown where Static does not say."""
        return (self.coordinator.data or {}).get(self._station_id)
