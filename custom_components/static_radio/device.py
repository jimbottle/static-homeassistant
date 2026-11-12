"""The one device every entity of this integration belongs to."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN, STATIC_URL, TITLE


def service_device(entry: ConfigEntry) -> DeviceInfo:
    """Static Radio (unofficial), shown as a service, linking to Static."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=TITLE,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=STATIC_URL,
    )
