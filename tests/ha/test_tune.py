"""Tuning, by every way of asking: the selector, the action and the four voice intents.

Speakers are stand-ins that record what they were asked to play. No stream is
opened and nothing is fetched but the saved schedule.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL, Response
from custom_components.static_radio.const import CONF_SPEAKERS, DOMAIN, TITLE
from custom_components.static_radio.stream import CONF_STREAM_KEYS
from custom_components.static_radio.tune import (
    NO_SPEAKER,
    WHICH_SPEAKER,
    TuneError,
    speaker_for,
    tuned_station,
)

START = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)
KITCHEN = "media_player.kitchen_speaker"
OFFICE = "media_player.office_speaker"
DIAL = ["Nature", "Work", "Learn", "News", "Weird", "Unite", "Make", "SHEILA B", "Sleep"]


@pytest.fixture(autouse=True)
def _quiet_world(freezer: FrozenDateTimeFactory) -> Iterator[None]:
    freezer.move_to(START)

    async def fetch(_url: str, _headers: Mapping[str, str]) -> Response:
        return Response(404, {}, b"")

    with patch("custom_components.static_radio.feed.feed_fetch", lambda _hass, _session: fetch):
        yield


@pytest.fixture
def played(hass: HomeAssistant) -> list[ServiceCall]:
    calls: list[ServiceCall] = async_mock_service(hass, "media_player", "play_media")
    return calls


async def _set_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, text: str, speakers: list[str]
) -> MockConfigEntry:
    aioclient_mock.get(SCHEDULE_URL, text=text)
    entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data={}, options={CONF_SPEAKERS: speakers})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _selector(hass: HomeAssistant, entry: MockConfigEntry, speaker: str) -> str:
    found = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN, f"{entry.entry_id}_{speaker}_station"
    )
    assert found is not None
    return found


async def _tune(hass: HomeAssistant, **data: object) -> None:
    await hass.services.async_call(DOMAIN, "tune", data, blocking=True)


async def _say(
    hass: HomeAssistant, name: str, station: str | None = None, device_id: str | None = None
) -> str:
    slots = {"station": {"value": station}} if station else {}
    response = await intent.async_handle(hass, "test", name, slots, device_id=device_id)
    spoken: str = response.speech["plain"]["speech"]
    return spoken


# -- the action ------------------------------------------------------------------


async def test_tuning_by_name_plays_that_stations_stream_on_the_home_network(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    entry = await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])

    await _tune(hass, entity_id=KITCHEN, station="news")

    key = entry.data[CONF_STREAM_KEYS][KITCHEN]
    assert played[0].data == {
        "entity_id": KITCHEN,
        "media_content_id": f"http://10.10.10.10:8123/api/static_radio/stream/{key}/news_01.flac",
        "media_content_type": "music",
    }
    assert hass.states.get(_selector(hass, entry, KITCHEN)).state == "News"  # type: ignore[union-attr]


async def test_with_one_speaker_set_up_it_need_not_be_named(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    await _tune(hass, station="Sleep")
    assert played[0].data["entity_id"] == KITCHEN
    assert "/sleep_01.flac" in played[0].data["media_content_id"]


async def test_steps_move_along_the_dial_and_stop_at_its_ends(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    entry = await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    selector = _selector(hass, entry, KITCHEN)

    def state() -> str:
        found = hass.states.get(selector)
        assert found is not None
        return found.state

    await _tune(hass, step=1)  # never tuned: any step lands on the first station
    assert state() == DIAL[0]
    await _tune(hass, step=1)
    assert state() == DIAL[1]
    await _tune(hass, step=-1)
    assert state() == DIAL[0]
    with pytest.raises(ServiceValidationError, match="Static Nature is the first station"):
        await _tune(hass, step=-1)
    await _tune(hass, station=DIAL[-1])
    with pytest.raises(ServiceValidationError, match="Static Sleep is the last station"):
        await _tune(hass, step=1)
    assert state() == DIAL[-1]
    assert len(played) == 4


async def test_with_no_station_it_comes_back_to_the_last_one(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    await _tune(hass)  # never tuned: the first on the dial
    assert "/nature_01.flac" in played[-1].data["media_content_id"]
    await _tune(hass, station="Weird")
    await _tune(hass)
    assert "/weird_01.flac" in played[-1].data["media_content_id"]


async def test_a_name_that_is_not_statics_is_refused_with_the_real_names(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    with pytest.raises(ServiceValidationError, match="Static's stations are Nature, Work"):
        await _tune(hass, station="Jazz")
    assert played == []


async def test_two_speakers_and_none_named_is_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN, OFFICE])
    with pytest.raises(ServiceValidationError, match=WHICH_SPEAKER):
        await _tune(hass, station="News")
    with pytest.raises(ServiceValidationError, match=NO_SPEAKER):
        await _tune(hass, entity_id="media_player.not_chosen", station="News")
    await _tune(hass, entity_id=OFFICE, station="News")
    assert [call.data["entity_id"] for call in played] == [OFFICE]


async def test_a_speaker_that_will_not_play_keeps_its_last_station(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, schedule_text_with_links: str
) -> None:
    entry = await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    calls = async_mock_service(hass, "media_player", "play_media")
    await _tune(hass, station="News")
    assert len(calls) == 1
    async_mock_service(
        hass, "media_player", "play_media", raise_exception=HomeAssistantError("unavailable")
    )
    with pytest.raises(HomeAssistantError):
        await _tune(hass, station="Sleep")
    assert hass.states.get(_selector(hass, entry, KITCHEN)).state == "News"  # type: ignore[union-attr]


# -- which action a speaker is asked through ---------------------------------------


async def test_a_music_assistant_player_is_asked_through_music_assistant(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    er.async_get(hass).async_get_or_create(
        "media_player", "music_assistant", "ma-1", suggested_object_id="kitchen_speaker"
    )
    through = async_mock_service(hass, "music_assistant", "play_media")
    entry = await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])

    await _tune(hass, station="News")

    key = entry.data[CONF_STREAM_KEYS][KITCHEN]
    assert played == []
    assert through[0].data == {
        "entity_id": KITCHEN,
        "media_id": f"http://10.10.10.10:8123/api/static_radio/stream/{key}/news_01.flac",
        "media_type": "radio",
    }


async def test_an_esphome_speaker_is_told_to_fetch_the_stream_itself(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    er.async_get(hass).async_get_or_create(
        "media_player", "esphome", "esp-1", suggested_object_id="kitchen_speaker"
    )
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    await _tune(hass, station="News")
    assert played[0].data["extra"] == {"bypass_proxy": True}


# -- the selector ------------------------------------------------------------------


async def test_choosing_on_the_selector_tunes_and_lets_the_stream_say_the_name(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    entry = await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _selector(hass, entry, KITCHEN), "option": "Learn"},
        blocking=True,
    )
    assert played[0].data["media_content_id"].endswith("/learn_01.flac")  # no "quiet"


# -- by voice ----------------------------------------------------------------------


async def test_voice_tunes_and_names_the_station_and_the_stream_does_not_repeat_it(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])

    assert await _say(hass, "StaticTune", "sheila b") == "Playing Static SHEILA B."
    assert played[-1].data["media_content_id"].endswith("/sheila_01.flac?quiet=1")
    assert await _say(hass, "StaticNext") == "Playing Static Sleep."
    assert await _say(hass, "StaticNext") == "Static Sleep is the last station on the dial."
    assert await _say(hass, "StaticPrevious") == "Playing Static SHEILA B."
    assert await _say(hass, "StaticPlay") == "Playing Static SHEILA B."
    assert (await _say(hass, "StaticTune", "Jazz")).startswith("Static's stations are Nature")
    assert len(played) == 4


async def test_every_spoken_reply_is_one_sentence_ending_in_a_period(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN, OFFICE])
    replies = [
        await _say(hass, "StaticPlay"),  # two speakers, no telling which
        await _say(hass, "StaticTune", "Jazz"),
        await _say(hass, "StaticPrevious"),
    ]
    for reply in replies:
        assert reply.endswith(".")
        assert "?" not in reply
        assert reply.count(". ") == 0
    assert replies[0] == WHICH_SPEAKER


async def test_voice_with_no_speaker_set_up_says_so(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, schedule_text_with_links: str
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [])
    assert await _say(hass, "StaticPlay") == NO_SPEAKER


async def test_a_speaker_that_fails_is_said_plainly(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, schedule_text_with_links: str
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    async_mock_service(
        hass, "media_player", "play_media", raise_exception=HomeAssistantError("unavailable")
    )
    assert await _say(hass, "StaticPlay") == "Static could not be started on that speaker."


async def test_whats_on_with_no_station_named_is_about_the_one_tuned(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    assert await _say(hass, "StaticWhatsOn") == "Say which Static station, for example Static News."
    await _say(hass, "StaticTune", "Nature")
    assert (await _say(hass, "StaticWhatsOn")).startswith("Static Nature is playing ")


# -- which speaker was meant -------------------------------------------------------


async def test_the_speaker_spoken_to_is_found_by_device_then_by_room(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    devices, entities, areas = dr.async_get(hass), er.async_get(hass), ar.async_get(hass)
    kitchen, office = areas.async_create("Kitchen"), areas.async_create("Office")
    # The kitchen's voice device is its own speaker. The office's is a separate
    # device from the player that makes sound there, in the same room.
    puck = devices.async_get_or_create(config_entry_id=owner.entry_id, identifiers={("t", "k")})
    devices.async_update_device(puck.id, area_id=kitchen.id)
    entities.async_get_or_create(
        "media_player", "esphome", "k", suggested_object_id="kitchen_speaker", device_id=puck.id
    )
    mic = devices.async_get_or_create(config_entry_id=owner.entry_id, identifiers={("t", "m")})
    devices.async_update_device(mic.id, area_id=office.id)
    player = entities.async_get_or_create(
        "media_player", "music_assistant", "o", suggested_object_id="office_speaker"
    )
    entities.async_update_entity(player.entity_id, area_id=office.id)
    elsewhere = devices.async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("t", "x")}
    )
    await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN, OFFICE])

    assert speaker_for(hass, puck.id) == KITCHEN
    assert speaker_for(hass, mic.id) == OFFICE
    with pytest.raises(TuneError, match=WHICH_SPEAKER):
        speaker_for(hass, elsewhere.id)
    with pytest.raises(TuneError, match=WHICH_SPEAKER):
        speaker_for(hass, None)

    async_mock_service(hass, "music_assistant", "play_media")
    assert await _say(hass, "StaticTune", "News", device_id=mic.id) == "Playing Static News."
    office_station = tuned_station(hass, OFFICE)
    assert office_station is not None
    assert office_station.display_name == "News"
    assert tuned_station(hass, KITCHEN) is None


# -- a speaker that drops off the network -------------------------------------------


async def _later(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _speaker(hass: HomeAssistant, state: str, title: str | None = "Static News") -> None:
    hass.states.async_set(KITCHEN, state, {"media_title": title} if title else {})
    await hass.async_block_till_done()


def _playing_static(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    state = hass.states.get(_selector(hass, entry, KITCHEN))
    assert state is not None
    return bool(state.attributes["playing_static"])


async def _on_the_news(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    text: str,
    freezer: FrozenDateTimeFactory,
) -> MockConfigEntry:
    """A speaker that has been playing Static News for a minute."""
    await _speaker(hass, "idle", None)
    entry = await _set_up(hass, aioclient_mock, text, [KITCHEN])
    await _tune(hass, station="News")
    await _speaker(hass, "playing")
    await _later(hass, freezer, 60)
    assert _playing_static(hass, entry)
    return entry


async def test_a_speaker_that_drops_off_and_returns_is_tuned_again_quietly(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    await _on_the_news(hass, aioclient_mock, schedule_text_with_links, freezer)

    await _speaker(hass, "unavailable", None)
    await _later(hass, freezer, 240)
    await _speaker(hass, "idle", None)
    assert len(played) == 1  # not at once: it has only just come back
    await _later(hass, freezer, 6)

    assert len(played) == 2
    assert played[1].data["media_content_id"].endswith("/news_01.flac?quiet=1")


async def test_a_speaker_gone_too_long_is_left_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    entry = await _on_the_news(hass, aioclient_mock, schedule_text_with_links, freezer)
    await _speaker(hass, "unavailable", None)
    await _later(hass, freezer, 11 * 60)
    await _speaker(hass, "idle", None)
    await _later(hass, freezer, 30)
    assert len(played) == 1
    assert not _playing_static(hass, entry)


@pytest.mark.parametrize(
    ("state", "title"),
    [("idle", None), ("paused", "Static News"), ("off", None), ("playing", "Jazz FM")],
)
async def test_a_speaker_that_was_stopped_or_put_on_something_else_is_not_tuned_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
    state: str,
    title: str | None,
) -> None:
    entry = await _on_the_news(hass, aioclient_mock, schedule_text_with_links, freezer)
    await _speaker(hass, state, title)
    assert not _playing_static(hass, entry)
    await _speaker(hass, "unavailable", None)
    await _later(hass, freezer, 60)
    await _speaker(hass, "idle", None)
    await _later(hass, freezer, 30)
    assert len(played) == 1


async def test_not_playing_yet_just_after_a_tune_is_not_taken_for_stopped(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    await _speaker(hass, "idle", None)
    entry = await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    await _tune(hass, station="News")
    await _speaker(hass, "idle", "click")  # the player has not caught up yet
    await _later(hass, freezer, 3)
    await _speaker(hass, "playing")
    assert _playing_static(hass, entry)


async def test_a_speaker_that_flickers_is_asked_once_when_it_stays(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
    freezer: FrozenDateTimeFactory,
) -> None:
    await _on_the_news(hass, aioclient_mock, schedule_text_with_links, freezer)
    await _speaker(hass, "unavailable", None)
    await _later(hass, freezer, 30)
    await _speaker(hass, "idle", None)
    await _later(hass, freezer, 2)
    await _speaker(hass, "unavailable", None)  # gone again before it settled
    await _later(hass, freezer, 20)
    assert len(played) == 1
    await _speaker(hass, "idle", None)
    await _later(hass, freezer, 6)
    assert len(played) == 2


async def test_a_station_played_from_the_media_browser_shows_on_the_speakers_selector(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    played: list[ServiceCall],
) -> None:
    from homeassistant.components import media_source  # noqa: PLC0415

    entry = await _set_up(hass, aioclient_mock, schedule_text_with_links, [KITCHEN])
    await media_source.async_resolve_media(hass, "media-source://static_radio/weird_01", KITCHEN)
    await hass.async_block_till_done()
    state = hass.states.get(_selector(hass, entry, KITCHEN))
    assert state is not None
    assert state.state == "Weird"
