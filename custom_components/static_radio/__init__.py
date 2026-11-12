"""Static Radio for Home Assistant (unofficial).

Plays Static Radio's (https://staticradio.app) live stations on Home Assistant
media players. This file is where Home Assistant starts and stops the
integration: it builds the one schedule client the whole install shares and
hands it to the coordinator.

Not affiliated with, endorsed by or supported by Static.
"""

from __future__ import annotations

import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .api import ScheduleClient
from .const import DOMAIN
from .coordinator import StaticCoordinator, session_fetch

type StaticConfigEntry = ConfigEntry[StaticCoordinator]

PLATFORMS = [Platform.SENSOR]


async def _schedule_client(hass: HomeAssistant) -> ScheduleClient:
    """The schedule client, made once and kept for as long as Home Assistant runs.

    It lives outside the config entry on purpose. The 15-minute floor on
    schedule requests is kept by the client, so a reload, or Home Assistant
    retrying a failed setup every few seconds, must find the same client and
    not a new one with no memory of when it last asked.
    """
    client: ScheduleClient | None = hass.data.get(DOMAIN)
    if client is None:
        integration = await async_get_integration(hass, DOMAIN)
        client = ScheduleClient(
            session_fetch(async_get_clientsession(hass)),
            str(integration.version),
            # Looked up now, not when api.py was imported, so a test that
            # freezes time also moves this clock.
            clock=time.monotonic,
        )
        hass.data[DOMAIN] = client
    return client


async def async_setup_entry(hass: HomeAssistant, entry: StaticConfigEntry) -> bool:
    """Fetch the schedule and keep it current."""
    coordinator = StaticCoordinator(hass, entry, await _schedule_client(hass))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StaticConfigEntry) -> bool:
    """Stop the timers. The schedule client stays, with its memory of the floor."""
    unloaded: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
