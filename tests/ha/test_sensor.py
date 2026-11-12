"""The now-playing sensors: the show, the episode and the two links.

The saved schedule carries no Support links, so by Static's guide each show's
link comes from its feed. The feed requests are answered by a fake that
records them; nothing here reaches a network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL, Response
from custom_components.static_radio.schedule import Live, Schedule, live, parse_schedule

# An instant inside the saved schedule's broadcast day.
FIXTURE_NOW = datetime(2026, 11, 11, 18, 0, 0, tzinfo=UTC)
SUPPORT = "https://example.org/support-this-show"
FEED_WITH_LINK = f'<rss><channel><podcast:funding url="{SUPPORT}">Support</podcast:funding><item/>'


class Feeds:
    """Stands in for every show's host: records what was asked, answers with a link."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, dict[str, str]]] = []

    async def fetch(self, url: str, headers: Mapping[str, str]) -> Response:
        self.asked.append((url, dict(headers)))
        return Response(200, {}, FEED_WITH_LINK.encode())

    @property
    def urls(self) -> list[str]:
        return [url for url, _headers in self.asked]


@pytest.fixture(autouse=True)
def feeds() -> Iterator[Feeds]:
    hosts = Feeds()
    with patch(
        "custom_components.static_radio.feed.feed_fetch", lambda _hass, _session: hosts.fetch
    ):
        yield hosts


@pytest.fixture(autouse=True)
def _inside_the_fixtures_day(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(FIXTURE_NOW)


@pytest.fixture
def schedule(schedule_text: str) -> Schedule:
    return parse_schedule(json.loads(schedule_text))


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def _live(schedule: Schedule, station_id: str, when: datetime) -> Live:
    station = schedule.station(station_id)
    assert station is not None
    found = live(schedule, station, _ms(when))
    assert found is not None
    return found


def _station_on(schedule: Schedule, when: datetime, *, bulletin: bool) -> str:
    """A station that is on a bulletin, or on a show, at that instant."""
    for station in schedule.stations:
        episode = _live(schedule, station.station_id, when).episode
        if episode.is_bulletin is bulletin and (bulletin or episode.feed_url):
            return station.station_id
    raise AssertionError("pick another instant")


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def _state(hass: HomeAssistant, entry: MockConfigEntry, station_id: str) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", "static_radio", f"{entry.entry_id}_{station_id}_now_playing"
    )
    assert entity_id is not None, station_id
    state = hass.states.get(entity_id)
    assert state is not None
    return state


async def _move_to(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: datetime) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_every_station_says_what_is_on(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    schedule: Schedule,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)

    assert len(schedule.stations) == 9
    for station in schedule.stations:
        episode = _live(schedule, station.station_id, FIXTURE_NOW).episode
        state = _state(hass, entry, station.station_id)
        assert state.state == episode.feed_title
        assert state.name == f"Static Radio (unofficial) {station.display_name}"
        assert state.attributes["episode"] == episode.title
        assert state.attributes["episode_url"] == (episode.episode_url or None)
        assert state.attributes["bulletin"] is episode.is_bulletin
        assert state.attributes["station_id"] == station.station_id


async def test_art_is_shown_only_when_it_is_on_the_web(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    schedule: Schedule,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)

    with_art = _state(hass, entry, "nature_01")
    assert with_art.attributes["entity_picture"].startswith("https://")

    # This station's art is built into Static's own apps: a path, not an address.
    bundled = _live(schedule, "sheila_01", FIXTURE_NOW).episode
    assert not bundled.image_url.startswith("https://")
    assert "entity_picture" not in _state(hass, entry, "sheila_01").attributes
    assert _state(hass, entry, "sheila_01").attributes["icon"] == "mdi:radio"


async def test_a_show_gets_its_support_link_from_its_feed_and_a_bulletin_gets_none(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    feeds: Feeds,
    freezer: FrozenDateTimeFactory,
) -> None:
    schedule = parse_schedule(json.loads(schedule_text))
    # An instant when at least one station is on a bulletin and another on a show.
    when = FIXTURE_NOW
    for minute in range(24 * 60):
        when = FIXTURE_NOW + timedelta(minutes=minute)
        on = [_live(schedule, s.station_id, when).episode for s in schedule.stations]
        if any(e.is_bulletin for e in on) and any(e.feed_url for e in on):
            break
    show_station = _station_on(schedule, when, bulletin=False)
    bulletin_station = _station_on(schedule, when, bulletin=True)

    freezer.move_to(when)
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)

    show = _state(hass, entry, show_station)
    assert show.attributes["support_url"] == SUPPORT
    assert show.attributes["episode_url"]
    assert show.attributes["bulletin"] is False

    bulletin = _state(hass, entry, bulletin_station)
    assert bulletin.attributes["bulletin"] is True
    assert bulletin.attributes["support_url"] is None

    # Only shows' feeds were asked, each once, and each with the User-Agent.
    live_feeds = {
        e.feed_url
        for e in (_live(schedule, s.station_id, when).episode for s in schedule.stations)
        if e.feed_url and not e.is_bulletin
    }
    assert sorted(feeds.urls) == sorted(live_feeds)
    assert all(h["User-Agent"].startswith("HomeAssistant-Static/") for _u, h in feeds.asked)


async def test_a_schedule_with_its_own_links_is_believed_and_no_feed_is_asked(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    schedule: Schedule,
    feeds: Feeds,
) -> None:
    station_id = _station_on(schedule, FIXTURE_NOW, bulletin=False)
    live_id = _live(schedule, station_id, FIXTURE_NOW).episode.episode_id
    raw: dict[str, Any] = json.loads(schedule_text)
    for episode in raw["categories"][station_id]["episodes"]:
        if episode["episodeId"] == live_id:
            episode["supportUrl"] = "https://example.org/from-the-schedule"
    aioclient_mock.get(SCHEDULE_URL, text=json.dumps(raw))
    await _set_up(hass, entry)

    state = _state(hass, entry, station_id)
    assert state.attributes["support_url"] == "https://example.org/from-the-schedule"
    # Every other show has no link in this file, which now means it has none.
    others = [s.station_id for s in schedule.stations if s.station_id != station_id]
    assert all(_state(hass, entry, s).attributes["support_url"] is None for s in others)
    assert feeds.asked == []


async def test_the_sensor_changes_when_the_station_moves_on(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    schedule: Schedule,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)

    # The station whose episode ends first.
    first = min(
        (_live(schedule, s.station_id, FIXTURE_NOW) for s in schedule.stations),
        key=lambda found: found.next_at_ms,
    )
    station_id = next(
        s.station_id
        for s in schedule.stations
        if _live(schedule, s.station_id, FIXTURE_NOW) == first
    )
    boundary = FIXTURE_NOW + timedelta(milliseconds=first.next_at_ms - _ms(FIXTURE_NOW))
    after = _live(schedule, station_id, boundary).episode
    assert after != first.episode

    await _move_to(hass, freezer, boundary - timedelta(seconds=1))
    assert _state(hass, entry, station_id).attributes["episode"] == first.episode.title

    await _move_to(hass, freezer, boundary)
    state = _state(hass, entry, station_id)
    assert state.state == after.feed_title
    assert state.attributes["episode"] == after.title
    assert state.attributes["bulletin"] is after.is_bulletin
    if after.is_bulletin:
        assert state.attributes["support_url"] is None


async def test_another_days_schedule_makes_the_sensors_unavailable(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)
    assert _state(hass, entry, "nature_01").state != STATE_UNAVAILABLE

    await _move_to(hass, freezer, FIXTURE_NOW + timedelta(hours=31))
    assert _state(hass, entry, "nature_01").state == STATE_UNAVAILABLE


async def test_a_reload_does_not_ask_any_feed_again(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    feeds: Feeds,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)
    asked = list(feeds.urls)
    assert asked

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert feeds.urls == asked  # each feed at most once a day, reload or not
    assert _state(hass, entry, "nature_01").state != STATE_UNAVAILABLE


async def test_unloading_stops_the_sensors(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    await _set_up(hass, entry)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, entry, "nature_01").state == STATE_UNAVAILABLE
    # The harness fails this test if a timer or task is still pending at the end.
