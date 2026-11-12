"""A station selector for each speaker chosen on the Configure screen.

Each selector lists Static's stations by Static's own names, in dial order,
and remembers the station last chosen for its speaker across restarts. That
memory is what "play Static" with no station named will come back to.

Choosing a station here does not start it playing yet. The part that plays a
station on a speaker is not built, and this file touches no audio: for now it
only keeps the choice.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import StaticConfigEntry
from .const import CONF_SPEAKERS
from .coordinator import StaticCoordinator
from .device import service_device
from .tuner import find_station


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaticConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One selector per chosen speaker."""
    async_add_entities(
        StationSelect(entry.runtime_data, entry, speaker, _speaker_name(hass, speaker))
        for speaker in entry.options.get(CONF_SPEAKERS, [])
    )


def _speaker_name(hass: HomeAssistant, entity_id: str) -> str:
    """What the speaker is called in Home Assistant, or its id if it is not loaded."""
    state = hass.states.get(entity_id)
    return state.name if state is not None else entity_id


class StationSelect(CoordinatorEntity[StaticCoordinator], SelectEntity, RestoreEntity):
    """The Static station chosen for one speaker."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:radio-tower"

    def __init__(
        self,
        coordinator: StaticCoordinator,
        entry: StaticConfigEntry,
        speaker: str,
        speaker_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.speaker = speaker
        self._attr_unique_id = f"{entry.entry_id}_{speaker}_station"
        self._attr_name = f"{speaker_name} station"
        self._attr_device_info = service_device(entry)
        self._attr_current_option = None

    async def async_added_to_hass(self) -> None:
        """Come back to the station chosen before the restart, if it is still on the dial."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.options:
            self._attr_current_option = last.state

    @property
    def options(self) -> list[str]:
        """Static's stations, by Static's names, in dial order."""
        return [station.display_name for station in self.coordinator.data.stations]

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data.stations)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {"speaker": self.speaker}

    async def async_select_option(self, option: str) -> None:
        """Keep the choice. Only one of Static's own names is accepted."""
        station = find_station(self.coordinator.data.stations, option)
        if station is None:
            raise ValueError(f"{option} is not a Static station")
        self._attr_current_option = station.display_name
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """A station that has left the dial is no longer the choice."""
        if self._attr_current_option not in self.options:
            self._attr_current_option = None
        super()._handle_coordinator_update()
