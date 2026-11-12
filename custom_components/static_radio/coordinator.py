"""Holds the day's schedule for the rest of the integration.

Three jobs:

- Ask api.py for the schedule every 15 minutes, and only while something is
  listening for it. A failed request keeps the copy in hand.
- Refuse another broadcast day's timeline. A schedule whose epoch is more than
  30 hours from now is never played, the same guard as Schedule.pm.
- Wake whatever shows now-playing at the moment a station moves to its next
  episode, so nothing has to poll the clock.

It touches no audio.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SCHEDULE_MIN_INTERVAL_S, Fetch, Response, ScheduleClient
from .const import DOMAIN
from .schedule import Live, Schedule, ScheduleError, parse_schedule
from .schedule import live as live_at
from .support import has_support_urls

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 30

# A little past the client's 15-minute floor. A tick that lands a moment early
# is answered from the copy in hand, which would stretch the wait to 30 minutes.
REFRESH_INTERVAL = timedelta(seconds=SCHEDULE_MIN_INTERVAL_S + 10)


def session_fetch(session: aiohttp.ClientSession) -> Fetch:
    """The one GET api.py asks for, performed with Home Assistant's session."""

    async def fetch(url: str, headers: Mapping[str, str]) -> Response:
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S)
        async with session.get(url, headers=dict(headers), timeout=timeout) as resp:
            return Response(resp.status, dict(resp.headers), await resp.read())

    return fetch


def now_ms() -> int:
    """The clock the timeline is read against, in milliseconds."""
    return int(dt_util.utcnow().timestamp() * 1000)


class StaticCoordinator(DataUpdateCoordinator[Schedule]):
    """The current schedule, and what is live on each station."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry[Any], client: ScheduleClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=REFRESH_INTERVAL,
        )
        self._client = client
        self._parsed_from: dict[str, Any] | None = None
        # Whether this schedule carries Support links itself. When it does
        # not, support.py reads each show's link from its feed instead.
        self.schedule_has_links = False
        self._cancel_boundary: CALLBACK_TYPE | None = None

    async def _async_update_data(self) -> Schedule:
        raw = await self._client.get()
        if raw is None:
            raise UpdateFailed("Static's schedule could not be fetched")
        if raw is self._parsed_from:
            return self.data  # a 304, or inside the floor: nothing new to read
        try:
            schedule = parse_schedule(raw)
        except ScheduleError as err:
            raise UpdateFailed(f"Static's schedule could not be used: {err}") from err
        self._parsed_from = raw
        self.schedule_has_links = has_support_urls(raw)
        return schedule

    def live(self, station_id: str, at_ms: int | None = None) -> Live | None:
        """What is on a station, or None when it must not be played.

        None for a station that is not on the dial, and for every station
        while the schedule in hand belongs to another broadcast day.
        """
        schedule: Schedule | None = self.data
        if schedule is None:
            return None
        at = now_ms() if at_ms is None else at_ms
        if not schedule.is_current_day(at):
            return None
        station = schedule.station(station_id)
        if station is None:
            return None
        return live_at(schedule, station, at)

    # -- waking listeners at episode boundaries ------------------------------

    @callback
    def async_add_listener(
        self, update_callback: CALLBACK_TYPE, context: Any = None
    ) -> Callable[[], None]:
        """Add a listener; it is also called when any station changes episode."""
        remove = super().async_add_listener(update_callback, context)
        self._arm_boundary()

        @callback
        def remove_listener() -> None:
            remove()
            if not self._listeners:
                self._disarm_boundary()

        return remove_listener

    @callback
    def async_update_listeners(self) -> None:
        """Tell the listeners, and aim the timer at the new schedule's next boundary."""
        super().async_update_listeners()
        self._arm_boundary()

    async def async_shutdown(self) -> None:
        """Cancel the timers when the entry unloads."""
        self._disarm_boundary()
        await super().async_shutdown()

    def _next_boundary_ms(self, at_ms: int) -> int | None:
        """When the next station moves on, or None with nothing playable."""
        schedule: Schedule | None = self.data
        if schedule is None or not schedule.is_current_day(at_ms):
            return None
        nexts = [
            found.next_at_ms
            for station in schedule.stations
            if (found := live_at(schedule, station, at_ms)) is not None
        ]
        return min(nexts, default=None)

    @callback
    def _disarm_boundary(self) -> None:
        if self._cancel_boundary is not None:
            self._cancel_boundary()
            self._cancel_boundary = None

    @callback
    def _arm_boundary(self) -> None:
        self._disarm_boundary()
        if not self._listeners or self._shutdown_requested:
            return
        boundary_ms = self._next_boundary_ms(now_ms())
        if boundary_ms is None:
            return

        @callback
        def fire(_now: datetime) -> None:
            self._cancel_boundary = None
            self.async_update_listeners()

        self._cancel_boundary = async_track_point_in_utc_time(
            self.hass, fire, dt_util.utc_from_timestamp(boundary_ms / 1000)
        )
