"""Setup, the schedule's upkeep and unloading, inside Home Assistant.

These hold the integration to what it promised Static at the point where it
meets a real HTTP session: one address, GET, the User-Agent, the 15-minute
floor through reloads and failed setups, and If-None-Match.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL
from custom_components.static_radio.const import DOMAIN
from custom_components.static_radio.coordinator import REFRESH_INTERVAL, StaticCoordinator

MANIFEST = json.loads(
    (
        Path(__file__).resolve().parents[2] / "custom_components/static_radio/manifest.json"
    ).read_text(encoding="utf-8")
)
STATION = "nature_01"

# An instant inside the saved schedule's broadcast day.
FIXTURE_NOW = datetime(2026, 11, 11, 18, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _inside_the_fixtures_day(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(FIXTURE_NOW)


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> StaticCoordinator:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator: StaticCoordinator = entry.runtime_data
    return coordinator


def _state(entry: MockConfigEntry) -> ConfigEntryState:
    """Read fresh each time, so mypy does not carry an earlier value forward."""
    state: ConfigEntryState = entry.state
    return state


def _headers(aioclient_mock: AiohttpClientMocker, call: int) -> dict[str, str]:
    return {k.lower(): v for k, v in aioclient_mock.mock_calls[call][3].items()}


async def test_setup_loads_the_schedule_with_one_polite_request(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    coordinator = await _set_up(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert len(coordinator.data.stations) == 9
    assert coordinator.live(STATION) is not None

    assert aioclient_mock.call_count == 1
    method, url, _data, _headers_sent = aioclient_mock.mock_calls[0]
    assert method.upper() == "GET"
    assert str(url) == SCHEDULE_URL
    sent = _headers(aioclient_mock, 0)
    assert sent["user-agent"] == f"HomeAssistant-Static/{MANIFEST['version']}"
    assert sent["accept-language"] == "en"
    assert "if-none-match" not in sent


async def test_a_reload_does_not_ask_static_again(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.runtime_data.data.stations) == 9
    assert aioclient_mock.call_count == 1


async def test_a_failed_setup_retries_without_asking_inside_the_floor(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, status=503)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(entry) is ConfigEntryState.SETUP_RETRY
    assert aioclient_mock.call_count == 1

    # Home Assistant retries within seconds. The floor still holds.
    for _ in range(3):
        freezer.tick(timedelta(minutes=2))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _state(entry) is ConfigEntryState.SETUP_RETRY
    assert aioclient_mock.call_count == 1

    # Past the floor, the next retry asks, and setup completes.
    aioclient_mock.clear_requests()
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    for _ in range(8):
        freezer.tick(timedelta(minutes=2))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _state(entry) is ConfigEntryState.LOADED
    assert aioclient_mock.call_count == 1


async def test_the_schedule_is_only_refreshed_while_something_listens(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text, headers={"ETag": '"day-1"'})
    coordinator = await _set_up(hass, entry)

    freezer.tick(REFRESH_INTERVAL * 3)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert aioclient_mock.call_count == 1  # nobody listening, nothing asked

    aioclient_mock.clear_requests()
    aioclient_mock.get(SCHEDULE_URL, status=304)
    before = coordinator.data
    remove = coordinator.async_add_listener(lambda: None)
    freezer.tick(REFRESH_INTERVAL + timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert aioclient_mock.call_count == 1
    assert _headers(aioclient_mock, 0)["if-none-match"] == '"day-1"'
    assert coordinator.data is before  # a 304 keeps the copy in hand
    assert coordinator.last_update_success
    remove()


async def test_listeners_are_woken_when_a_station_changes_episode(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    coordinator = await _set_up(hass, entry)
    now_ms = int(FIXTURE_NOW.timestamp() * 1000)
    boundaries = sorted(
        found.next_at_ms
        for station in coordinator.data.stations
        if (found := coordinator.live(station.station_id, now_ms)) is not None
    )
    first = boundaries[0]
    assert first - now_ms < REFRESH_INTERVAL.total_seconds() * 1000, "pick another instant"

    woken: list[int] = []
    remove = coordinator.async_add_listener(lambda: woken.append(1))

    freezer.move_to(FIXTURE_NOW + timedelta(milliseconds=first - now_ms - 1000))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert woken == []

    freezer.move_to(FIXTURE_NOW + timedelta(milliseconds=first - now_ms))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert woken == [1]
    remove()


async def test_another_days_schedule_is_never_live(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    coordinator = await _set_up(hass, entry)
    assert coordinator.live(STATION) is not None
    assert coordinator.live("no_such_station") is None

    freezer.move_to(FIXTURE_NOW + timedelta(hours=31))
    assert coordinator.live(STATION) is None


async def test_unloading_leaves_nothing_running(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    coordinator = await _set_up(hass, entry)
    coordinator.async_add_listener(lambda: None)  # never removed, as a careless entity would

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    # The harness fails this test if a timer is still pending at the end.

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN) == []
