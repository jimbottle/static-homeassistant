"""The two voice intents: what is on, and the show's Support link.

Run against the first saved schedule that carries Support links itself, so
the links are real ones and no show's feed is asked.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, llm
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL, Response
from custom_components.static_radio.intents import (
    INTENT_SUPPORT_LINK,
    INTENT_WHATS_ON,
    spoken_link,
)
from custom_components.static_radio.schedule import Episode, live, parse_schedule

# An instant inside the broadcast day of the 2026-11-12 schedule.
START = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def feeds_asked() -> Iterator[list[str]]:
    asked: list[str] = []

    async def fetch(url: str, _headers: Mapping[str, str]) -> Response:
        asked.append(url)
        return Response(404, {}, b"")

    with patch("custom_components.static_radio.feed.feed_fetch", lambda _hass, _session: fetch):
        yield asked


def _on_air(schedule_text: str, when: datetime) -> dict[str, Episode]:
    schedule = parse_schedule(json.loads(schedule_text))
    at_ms = int(when.timestamp() * 1000)
    on_air: dict[str, Episode] = {}
    for station in schedule.stations:
        found = live(schedule, station, at_ms)
        assert found is not None
        on_air[station.display_name] = found.episode
    return on_air


def _find(schedule_text: str, kind: str) -> tuple[datetime, str, Episode]:
    """An instant, and a station that is then on a show with a link, without one, or a bulletin."""
    for minute in range(0, 20 * 60, 3):
        when = START + timedelta(minutes=minute)
        for name, episode in _on_air(schedule_text, when).items():
            is_kind = {
                "link": bool(episode.support_url) and not episode.is_bulletin,
                "no link": not episode.support_url and not episode.is_bulletin,
                "bulletin": episode.is_bulletin,
            }[kind]
            if is_kind:
                return when, name, episode
    raise AssertionError(f"the saved schedule never has: {kind}")


async def _set_up(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=schedule_text)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _ask(hass: HomeAssistant, intent_type: str, station: str | None) -> intent.IntentResponse:
    slots = {"station": {"value": station}} if station is not None else {}
    return await intent.async_handle(hass, "test", intent_type, slots)


def _said(response: intent.IntentResponse) -> str:
    said: str = response.speech["plain"]["speech"]
    # The house rule for every reply: one sentence, a period, never a question.
    assert said.endswith(".")
    assert "?" not in said
    assert "\n" not in said
    return said


async def test_whats_on_names_the_show_and_mentions_its_support_link(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, name, episode = _find(schedule_text_with_links, "link")
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    said = _said(await _ask(hass, INTENT_WHATS_ON, name))

    assert said.startswith(f"Static {name} is playing ")
    assert episode.feed_title.rstrip(".?! ") in said
    assert said.endswith(", and the show has a Support link.")

    # Now-playing for a text or language-model assistant: both links, together.
    for_a_model = llm.IntentResponseDict(await _ask(hass, INTENT_WHATS_ON, name))
    assert for_a_model["speech_slots"] == {
        "episode_url": episode.episode_url,
        "support_url": episode.support_url,
    }


async def test_whats_on_without_a_support_link_does_not_mention_one(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, name, episode = _find(schedule_text_with_links, "no link")
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    said = _said(await _ask(hass, INTENT_WHATS_ON, name))

    assert episode.feed_title.rstrip(".?! ") in said
    assert "Support" not in said


async def test_a_bulletin_is_called_a_bulletin_and_has_no_support_link(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, name, _episode = _find(schedule_text_with_links, "bulletin")
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    assert _said(await _ask(hass, INTENT_WHATS_ON, name)) == (
        f"Static {name} is playing a community bulletin."
    )
    response = await _ask(hass, INTENT_SUPPORT_LINK, name)
    assert _said(response) == (
        f"Static {name} is playing a community bulletin, which has no Support link."
    )
    assert response.card == {}
    assert "support_url" not in llm.IntentResponseDict(response).get("speech_slots", {})
    whats_on = llm.IntentResponseDict(await _ask(hass, INTENT_WHATS_ON, name))
    assert "support_url" not in whats_on.get("speech_slots", {})


async def test_the_support_link_is_said_and_attached_in_full(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, name, episode = _find(schedule_text_with_links, "link")
    assert episode.support_url is not None
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    # An assistant may pass the name in any case, or the station's id.
    response = await _ask(hass, INTENT_SUPPORT_LINK, name.lower())

    said = _said(response)
    assert said.startswith("You can support ")
    assert spoken_link(episode.support_url) in said
    assert "https://" not in said
    assert response.card["simple"] == {"title": "Support", "content": episode.support_url}

    # An assistant with a language model is given this, without the card. The
    # whole address has to be in it, not only the shortened one that is spoken.
    for_a_model = llm.IntentResponseDict(response)
    assert "card" not in for_a_model
    assert for_a_model["speech_slots"]["support_url"] == episode.support_url
    assert for_a_model["speech_slots"]["episode_url"] == episode.episode_url


async def test_a_show_without_a_support_page_says_so_and_asks_no_feed(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
    feeds_asked: list[str],
) -> None:
    when, name, _episode = _find(schedule_text_with_links, "no link")
    freezer.move_to(when)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    said = _said(await _ask(hass, INTENT_SUPPORT_LINK, name))

    assert said.endswith(" has no Support link.")
    # This schedule carries links, so a show without one has none: no feed is read.
    assert feeds_asked == []


async def test_before_setup_the_intents_say_static_is_not_set_up(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(START)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert _said(await _ask(hass, INTENT_WHATS_ON, "News")) == "Static Radio is not set up."


async def test_a_missing_or_unknown_station_and_another_days_schedule(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to(START)
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    for intent_type in (INTENT_WHATS_ON, INTENT_SUPPORT_LINK):
        assert _said(await _ask(hass, intent_type, None)) == (
            "Say which Static station, for example Static News."
        )
        assert _said(await _ask(hass, intent_type, "Jazz?")) == (
            "Static's stations are Nature, Work, Learn, News, Weird, Unite, Make, "
            "SHEILA B and Sleep."
        )

    freezer.move_to(START + timedelta(hours=48))  # more than 30 h past its epoch
    assert _said(await _ask(hass, INTENT_WHATS_ON, "News")) == (
        "Static's schedule is not available right now."
    )


@pytest.mark.parametrize(
    ("url", "spoken"),
    [
        ("https://www.patreon.com/example/", "patreon.com/example"),
        ("http://example.org", "example.org"),
        ("https://example.com/a/b?c=1", "example.com/a/b?c=1"),
    ],
)
def test_a_link_is_said_without_its_scheme(url: str, spoken: str) -> None:
    assert spoken_link(url) == spoken
