"""A station selector for each speaker chosen on the Configure screen.

Each selector lists Static's stations by Static's own names, in dial order,
and remembers the station last chosen for its speaker across restarts. That
memory is what "play Static" with no station named will come back to.

Choosing a station here tunes the speaker to it: the stream is started
(play.py) and the choice is kept only once the speaker has taken it. Every
other way of tuning (tune.py) goes through the same selector, so the
dashboard always shows what the speaker was last asked to play.

Each selector also keeps watch on its speaker (resume.py): one that drops off
the network while playing Static is tuned again when it returns. The
"playing_static" attribute says whether the speaker is, as far as can be told.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import StaticConfigEntry
from .const import CONF_SPEAKERS
from .coordinator import StaticCoordinator
from .device import service_device
from .play import async_play
from .resume import OnAir
from .schedule import Station
from .tune import SELECTORS_KEY
from .tuner import find_station


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StaticConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One selector per chosen speaker, and none left over for a speaker taken off the list."""
    speakers: list[str] = entry.options.get(CONF_SPEAKERS, [])
    wanted = {_unique_id(entry, speaker) for speaker in speakers}
    registry = er.async_get(hass)
    for known in er.async_entries_for_config_entry(registry, entry.entry_id):
        if known.domain == "select" and known.unique_id not in wanted:
            registry.async_remove(known.entity_id)
    async_add_entities(
        StationSelect(entry.runtime_data, entry, speaker, _speaker_name(hass, speaker))
        for speaker in speakers
    )


def _unique_id(entry: StaticConfigEntry, speaker: str) -> str:
    return f"{entry.entry_id}_{speaker}_station"


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
        self._entry = entry
        self.speaker = speaker
        self._attr_unique_id = _unique_id(entry, speaker)
        self._attr_name = f"{speaker_name} station"
        self._attr_device_info = service_device(entry)
        self._attr_current_option = None
        self._on_air: OnAir | None = None

    async def async_added_to_hass(self) -> None:
        """Come back to the station chosen before the restart, if it is still on the dial."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in self.options:
            self._attr_current_option = last.state
        self.hass.data.setdefault(SELECTORS_KEY, {})[self.speaker] = self
        self._on_air = OnAir(self.hass, self.speaker, self._tune_again, self.async_write_ha_state)

    async def _tune_again(self) -> None:
        """Back on the station it was on, quietly, after the speaker dropped off."""
        station = self.station
        if station is not None:
            await self.async_tune(station, say=False)

    async def async_will_remove_from_hass(self) -> None:
        if self._on_air is not None:
            self._on_air.stop()
        selectors = self.hass.data.get(SELECTORS_KEY, {})
        if selectors.get(self.speaker) is self:
            del selectors[self.speaker]
        await super().async_will_remove_from_hass()

    @property
    def station(self) -> Station | None:
        """The station this speaker was last tuned to, if it is still on the dial."""
        current = self._attr_current_option
        if current is None:
            return None
        return find_station(self.coordinator.data.stations, current)

    async def async_tune(self, station: Station, *, say: bool) -> None:
        """Start the station on the speaker, then keep it as the choice."""
        await async_play(self.hass, self._entry, self.speaker, station, say=say)
        self.note(station)

    @callback
    def note(self, station: Station) -> None:
        """Keep a station as the choice: the speaker has been handed its stream."""
        self._attr_current_option = station.display_name
        if self._on_air is not None:
            self._on_air.tuned()
        self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        """Static's stations, by Static's names, in dial order."""
        return [station.display_name for station in self.coordinator.data.stations]

    @property
    def available(self) -> bool:
        return bool(self.coordinator.data.stations)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "speaker": self.speaker,
            "playing_static": self._on_air is not None and self._on_air.on,
        }

    async def async_select_option(self, option: str) -> None:
        """Tune the speaker. Only one of Static's own names is accepted."""
        station = find_station(self.coordinator.data.stations, option)
        if station is None:
            raise ValueError(f"{option} is not a Static station")
        await self.async_tune(station, say=True)

    @callback
    def _handle_coordinator_update(self) -> None:
        """A station that has left the dial is no longer the choice."""
        if self._attr_current_option not in self.options:
            self._attr_current_option = None
        super()._handle_coordinator_update()
