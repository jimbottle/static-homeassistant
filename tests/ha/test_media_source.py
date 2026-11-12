"""The media browser: stations by name, and inside each, what is on with its links.

Now-playing never appears without the episode and Support addresses beside
it, a bulletin never has a Support entry, and nothing can be played yet.
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
from custom_components.static_radio.schedule import Episode, live, parse_schedule

FIXTURE_NOW = datetime(2026, 11, 11, 18, 0, 0, tzinfo=UTC)
LINKS_NOW = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)  # inside the 2026-11-12 schedule's day
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
        # It opens to show what is on; nothing plays until the stream exists.
        assert child.can_expand is True
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

    # And nothing in the listing, or inside any station, is an episode's audio address.
    root = await media_source.async_browse_media(hass, ROOT)
    assert root.children is not None
    inside = [
        (await media_source.async_browse_media(hass, child.media_content_id)).as_dict()
        for child in root.children
    ]
    audio = {
        e["audioUrl"]
        for c in json.loads(schedule_text)["categories"].values()
        for e in c["episodes"]
    }
    listing = json.dumps([root.as_dict(), inside])
    assert not any(url in listing for url in audio)


def _find(text: str, kind: str) -> tuple[datetime, str, Episode]:
    """An instant and a station id then on a show with a link, without one, or a bulletin."""
    schedule = parse_schedule(json.loads(text))
    for minute in range(0, 20 * 60, 3):
        when = LINKS_NOW + timedelta(minutes=minute)
        for station in schedule.stations:
            found = live(schedule, station, int(when.timestamp() * 1000))
            assert found is not None
            episode = found.episode
            is_kind = {
                "link": bool(episode.support_url) and not episode.is_bulletin,
                "no link": not episode.support_url and not episode.is_bulletin,
                "bulletin": episode.is_bulletin,
            }[kind]
            if is_kind:
                return when, station.station_id, episode
    raise AssertionError(f"the saved schedule never has: {kind}")


async def test_a_station_opens_to_what_is_on_with_both_links_beneath(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, station_id, episode = _find(schedule_text_with_links, "link")
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    station = await media_source.async_browse_media(hass, f"{ROOT}/{station_id}")

    assert episode.feed_title in station.title
    assert episode.title in station.title
    assert station.thumbnail == episode.image_url
    assert station.can_play is False
    assert station.children is not None
    assert [child.title for child in station.children] == [
        f"Episode page: {episode.episode_url}",
        f"Support: {episode.support_url}",
    ]
    assert all(not c.can_play and not c.can_expand for c in station.children)


async def test_a_show_without_a_support_page_has_no_support_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, station_id, _episode = _find(schedule_text_with_links, "no link")
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    station = await media_source.async_browse_media(hass, f"{ROOT}/{station_id}")
    assert station.children is not None
    assert not any(child.title.startswith("Support") for child in station.children)


async def test_a_bulletin_is_named_as_one_and_never_has_a_support_entry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, station_id, episode = _find(schedule_text_with_links, "bulletin")
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    station = await media_source.async_browse_media(hass, f"{ROOT}/{station_id}")
    assert station.title.endswith("· Community bulletin")
    assert station.children is not None
    assert not any(child.title.startswith("Support") for child in station.children)
    if episode.episode_url:
        assert station.children[0].title == f"Episode page: {episode.episode_url}"


async def test_only_stations_on_the_dial_open(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    await _set_up(hass, entry, aioclient_mock, schedule_text)
    for identifier in ("jazz_01", "nature_01/support", "nature_01/episodes"):
        with pytest.raises(BrowseError):
            await media_source.async_browse_media(hass, f"{ROOT}/{identifier}")


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
