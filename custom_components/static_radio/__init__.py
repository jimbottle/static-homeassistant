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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import ScheduleClient
from .const import DOMAIN
from .coordinator import SCHEMA_ISSUE, StaticCoordinator, session_fetch
from .intents import async_setup_intents
from .schedule import parse_schedule

type StaticConfigEntry = ConfigEntry[StaticCoordinator]

PLATFORMS = [Platform.SELECT, Platform.SENSOR]

# Set up from the UI only; there is nothing to put in configuration.yaml.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register what a voice assistant can ask, once for the life of Home Assistant."""
    async_setup_intents(hass)
    return True


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
            # A file with nothing playable in it never replaces one that has.
            check=parse_schedule,
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
        # A notice about the schedule's format belongs to a loaded entry. A
        # reload raises it again from the schedule in hand if it still applies.
        ir.async_delete_issue(hass, DOMAIN, SCHEMA_ISSUE)
    return unloaded
