"""The listeners sensors: off by default, and polite to Static when on.

Static asks for the counts at most once a minute. These tests hold the
integration to that, and to asking nothing while no listeners sensor is on.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import COUNTS_URL, SCHEDULE_URL, Response

FIXTURE_NOW = datetime(2026, 11, 11, 18, 0, 0, tzinfo=UTC)
COUNTS = {"counts": {"nature_01": 12, "news_01": 3, "sleep_01": None}}


@pytest.fixture(autouse=True)
def _no_feeds() -> Iterator[None]:
    """Support links are not what is tested here; no show's feed is asked."""

    async def fetch(_url: str, _headers: object) -> Response:
        return Response(404, {}, b"")

    with patch("custom_components.static_radio.feed.feed_fetch", lambda _hass, _session: fetch):
        yield


@pytest.fixture(autouse=True)
def _inside_the_fixtures_day(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(FIXTURE_NOW)


def _unique_id(entry: MockConfigEntry, station_id: str) -> str:
    return f"{entry.entry_id}_{station_id}_listeners"


def _switch_on(hass: HomeAssistant, entry: MockConfigEntry, *station_ids: str) -> None:
    """Register the sensors as enabled before setup, as a listener's choice would leave them."""
    registry = er.async_get(hass)
    for station_id in station_ids:
        registry.async_get_or_create(
            "sensor", "static_radio", _unique_id(entry, station_id), config_entry=entry
        )


def _state(hass: HomeAssistant, entry: MockConfigEntry, station_id: str) -> State | None:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", "static_radio", _unique_id(entry, station_id)
    )
    assert entity_id is not None, station_id
    return hass.states.get(entity_id)


def _counts_requests(aioclient_mock: AiohttpClientMocker) -> list[dict[str, str]]:
    return [
        dict(headers)
        for method, url, _data, headers in aioclient_mock.mock_calls
        if str(url) == COUNTS_URL and method.upper() == "GET"
    ]


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _pass(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    for _ in range(seconds // 5):
        freezer.tick(timedelta(seconds=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_nothing_is_asked_until_a_listeners_sensor_is_switched_on(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    aioclient_mock.get(COUNTS_URL, text=json.dumps(COUNTS))
    await _set_up(hass, entry)
    await _pass(hass, freezer, 600)

    # The sensor exists for the listener to switch on, and is off.
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", "static_radio", _unique_id(entry, "nature_01")
    )
    assert entity_id is not None
    found = registry.async_get(entity_id)
    assert found is not None
    assert found.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert _counts_requests(aioclient_mock) == []


async def test_a_switched_on_sensor_shows_statics_number(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    aioclient_mock.get(COUNTS_URL, text=json.dumps(COUNTS))
    _switch_on(hass, entry, "nature_01", "sleep_01", "work_01")
    await _set_up(hass, entry)

    nature = _state(hass, entry, "nature_01")
    assert nature is not None
    assert nature.state == "12"
    assert nature.attributes["unit_of_measurement"] == "listeners"
    assert nature.name == "Static Radio (unofficial) Nature listeners"

    # Static sent null for one station and left another out: both are unknown.
    for station_id in ("sleep_01", "work_01"):
        state = _state(hass, entry, station_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN

    # Three sensors came on together and Static was asked once.
    asked = _counts_requests(aioclient_mock)
    assert len(asked) == 1
    assert asked[0]["User-Agent"].startswith("HomeAssistant-Static/")


async def test_the_counts_are_asked_for_at_most_once_a_minute(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    aioclient_mock.get(COUNTS_URL, text=json.dumps(COUNTS))
    _switch_on(hass, entry, "nature_01", "news_01")
    await _set_up(hass, entry)

    await _pass(hass, freezer, 3600)

    asked = len(_counts_requests(aioclient_mock))
    assert 50 <= asked <= 60, asked


async def test_a_failed_request_keeps_the_last_number(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    aioclient_mock.get(COUNTS_URL, text=json.dumps(COUNTS))
    _switch_on(hass, entry, "nature_01")
    await _set_up(hass, entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(COUNTS_URL, status=503)
    await _pass(hass, freezer, 180)

    assert len(_counts_requests(aioclient_mock)) >= 2
    state = _state(hass, entry, "nature_01")
    assert state is not None
    assert state.state == "12"


async def test_a_reload_does_not_ask_again_inside_the_minute(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    aioclient_mock.get(COUNTS_URL, text=json.dumps(COUNTS))
    _switch_on(hass, entry, "nature_01")
    await _set_up(hass, entry)

    for _ in range(3):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert len(_counts_requests(aioclient_mock)) == 1
    state = _state(hass, entry, "nature_01")
    assert state is not None
    assert state.state == "12"
