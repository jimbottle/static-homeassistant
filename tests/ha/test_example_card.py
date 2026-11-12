"""The example dashboard card renders the links Static asks for.

The card in examples/ is what most people will copy, so it is rendered here
against real sensor states: an Episode link and a Support link side by side
for a show, no Support link for a bulletin, and the credit to Static.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL, Response
from custom_components.static_radio.schedule import Episode, live, parse_schedule

CARD = Path(__file__).resolve().parents[2] / "examples/now-playing-card.yaml"
FIXTURE_NOW = datetime(2026, 11, 11, 18, 0, 0, tzinfo=UTC)
SUPPORT = "https://example.org/support-this-show"


@pytest.fixture(autouse=True)
def _every_show_has_a_support_page() -> Iterator[None]:
    async def fetch(_url: str, _headers: Mapping[str, str]) -> Response:
        feed = f'<rss><channel><podcast:funding url="{SUPPORT}">Support</podcast:funding><item/>'
        return Response(200, {}, feed.encode())

    with patch("custom_components.static_radio.feed.feed_fetch", lambda _hass, _session: fetch):
        yield


def _on_air(schedule_text: str, when: datetime) -> dict[str, Episode]:
    schedule = parse_schedule(json.loads(schedule_text))
    at_ms = int(when.timestamp() * 1000)
    on_air: dict[str, Episode] = {}
    for station in schedule.stations:
        found = live(schedule, station, at_ms)
        assert found is not None
        on_air[station.display_name] = found.episode
    return on_air


async def test_the_card_shows_both_links_for_a_show_and_no_support_link_for_a_bulletin(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    # An instant with a bulletin on one station and shows on others.
    when = FIXTURE_NOW
    for minute in range(24 * 60):
        when = FIXTURE_NOW + timedelta(minutes=minute)
        if any(e.is_bulletin for e in _on_air(schedule_text, when).values()):
            break
    on_air = _on_air(schedule_text, when)
    freezer.move_to(when)
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    card = yaml.safe_load(CARD.read_text(encoding="utf-8"))
    assert card["type"] == "markdown"
    rendered = Template(card["content"], hass).async_render()

    assert len(on_air) == 9
    for name, episode in on_air.items():
        start = rendered.index(f"**{name}**")
        following = [rendered.index(f"**{other}**") for other in on_air if other != name]
        end = min((i for i in following if i > start), default=rendered.index("Stations by"))
        block = rendered[start:end]
        assert episode.feed_title in block
        assert episode.title in block
        if episode.episode_url:
            assert f"[Episode]({episode.episode_url})" in block
        if episode.is_bulletin:
            assert "[Support]" not in block
        elif episode.feed_url:
            assert f"[Episode]({episode.episode_url}) · [Support]({SUPPORT})" in block

    assert "[Static Radio](https://staticradio.app)" in rendered
    assert "unofficial" in rendered
