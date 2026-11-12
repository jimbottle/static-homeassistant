"""How many people are listening to each station, kept current while shown.

Static publishes live listener counts and asks that they be requested at most
once a minute. This file asks only while a listeners sensor is enabled and
loaded, a little slower than the limit, and keeps the one counts client for
the life of Home Assistant so a reload cannot ask again inside the minute.

The count is Static's own number, shown as published: it does not include
anyone listening through this integration.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.loader import async_get_integration

from .api import COUNTS_MIN_INTERVAL_S, CountsClient
from .const import DOMAIN
from .coordinator import session_fetch

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

# A little past the client's one-minute floor. A tick that lands a moment
# early is answered from the last numbers, which would stretch the wait to two.
COUNTS_INTERVAL = timedelta(seconds=COUNTS_MIN_INTERVAL_S + 5)

_COUNTS_KEY = f"{DOMAIN}.counts"


async def counts_client(hass: HomeAssistant) -> CountsClient:
    """The counts client, made once and kept while Home Assistant runs."""
    client: CountsClient | None = hass.data.get(_COUNTS_KEY)
    if client is None:
        integration = await async_get_integration(hass, DOMAIN)
        client = CountsClient(
            session_fetch(async_get_clientsession(hass)),
            str(integration.version),
            # Looked up now, so a test that freezes time also moves this clock.
            clock=time.monotonic,
        )
        hass.data[_COUNTS_KEY] = client
    return client


class CountsCoordinator(DataUpdateCoordinator[dict[str, int | None]]):
    """Listeners by station id. Asks Static only while a sensor is listening."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry[Any], client: CountsClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} listeners",
            update_interval=COUNTS_INTERVAL,
        )
        self._client = client

    async def _async_update_data(self) -> dict[str, int | None]:
        # The client never raises: a failed request keeps the last numbers.
        return await self._client.get()
