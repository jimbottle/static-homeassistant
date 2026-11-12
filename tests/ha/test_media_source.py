"""The media browser lists the stations by name, and plays nothing yet.

It does not say what is on: an entry has nowhere to put the episode and
Support links that Static asks to see beside now-playing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import media_source
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL, Response
from custom_components.static_radio.schedule import live, parse_schedule

FIXTURE_NOW = datetime(2026, 11, 11, 18, 0, 0, tzinfo=UTC)
ROOT = "media-source://static_radio"


@pytest.fixture(autouse=True)
def _no_feeds() -> Iterator[None]:
    async def fetch(_url: str, _headers: Mapping[str, str]) -> Response:
        return Response(404, {}, b"")

    with patch("custom_components.static_radio.feed.feed_fetch", lambda _hass, _session: fetch):
        yield


@pytest.fixture(autouse=True)
def _inside_the_fixtures_day(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(FIXTURE_NOW)


async def _set_up(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, text: str
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=text)
    assert await async_setup_component(hass, "media_source", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_the_browser_lists_every_station_by_name_and_not_what_is_on(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    await _set_up(hass, entry, aioclient_mock, schedule_text)
    schedule = parse_schedule(json.loads(schedule_text))
    now_ms = int(FIXTURE_NOW.timestamp() * 1000)

    root = await media_source.async_browse_media(hass, ROOT)

    assert root.title == "Static Radio (unofficial)"
    assert root.can_play is False
    assert root.children is not None
    assert len(root.children) == len(schedule.stations) == 9
    for child, station in zip(root.children, schedule.stations, strict=True):  # dial order
        found = live(schedule, station, now_ms)
        assert found is not None
        assert child.media_content_id == f"{ROOT}/{station.station_id}"
        assert child.title == station.display_name
        # Now-playing without its links is not shown: no show, no episode, no art.
        assert child.thumbnail is None
        # Live radio: nothing to open, and nothing to play until the stream exists.
        assert child.can_expand is False
        assert child.can_play is False


async def test_no_audio_address_is_handed_out(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    await _set_up(hass, entry, aioclient_mock, schedule_text)

    with pytest.raises(media_source.Unresolvable):
        await media_source.async_resolve_media(hass, f"{ROOT}/nature_01", None)

    # And nothing in the listing is an episode's audio address.
    root = await media_source.async_browse_media(hass, ROOT)
    audio = {
        e["audioUrl"]
        for c in json.loads(schedule_text)["categories"].values()
        for e in c["episodes"]
    }
    listing = json.dumps(root.as_dict())
    assert not any(url in listing for url in audio)


async def test_a_station_has_nothing_inside_it(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    await _set_up(hass, entry, aioclient_mock, schedule_text)
    with pytest.raises(BrowseError):
        await media_source.async_browse_media(hass, f"{ROOT}/nature_01")


async def test_another_days_schedule_lists_no_stations(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _set_up(hass, entry, aioclient_mock, schedule_text)
    freezer.move_to(FIXTURE_NOW + timedelta(hours=31))

    root = await media_source.async_browse_media(hass, ROOT)
    assert root.children == []


async def test_the_browser_is_empty_while_the_integration_is_not_loaded(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    await _set_up(hass, entry, aioclient_mock, schedule_text)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    root = await media_source.async_browse_media(hass, ROOT)
    assert root.children == []
