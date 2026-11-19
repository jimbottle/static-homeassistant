"""The volume dial as a tuning dial, on a speaker whose firmware was not changed.

The speaker is two stand-ins, as in a home that plays through Music Assistant:
the player that is tuned, and the device's own player, whose volume is all the
dial shows of itself. A stand-in for the volume action moves the device's
volume the way the device would report it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL, Response
from custom_components.static_radio.const import CONF_DIALS, CONF_SPEAKERS, DOMAIN, TITLE
from custom_components.static_radio.dial import ARMED_S, REST_S, clicks_in, volume_player_for

START = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)
SPEAKER = "media_player.kitchen_speaker"  # the player that is tuned
OWN = "media_player.kitchen_puck_media_player"  # the device's own player
BUTTON = "event.kitchen_puck_button_press"
HOME = 0.25  # the volume the speaker is at


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


@pytest.fixture
def put_back(hass: HomeAssistant) -> list[float]:
    """Every volume the speaker was set to. The device then reports it, as a real one does."""
    asked: list[float] = []

    async def volume_set(call: ServiceCall) -> None:
        asked.append(call.data["volume_level"])
        hass.states.async_set(OWN, "idle", {"volume_level": call.data["volume_level"]})

    hass.services.async_register("media_player", "volume_set", volume_set)
    return asked


async def _set_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, text: str, *, dial: bool = True
) -> MockConfigEntry:
    aioclient_mock.get(SCHEDULE_URL, text=text)
    options = {CONF_SPEAKERS: [SPEAKER], CONF_DIALS: {SPEAKER: BUTTON} if dial else {}}
    entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data={}, options=options)
    entry.add_to_hass(hass)
    puck = MockConfigEntry(domain="esphome")
    puck.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=puck.entry_id, identifiers={("esphome", "puck")}
    )
    entities = er.async_get(hass)
    for entity_id in (OWN, BUTTON):
        domain, name = entity_id.split(".")
        entities.async_get_or_create(
            domain, "esphome", name, suggested_object_id=name, device_id=device.id
        )
    hass.states.async_set(OWN, "idle", {"volume_level": HOME})
    hass.states.async_set(BUTTON, "2026-11-12T17:00:00.000+00:00", {"event_type": "triple_press"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _press(hass: HomeAssistant, freezer: FrozenDateTimeFactory, kind: str) -> None:
    freezer.tick(timedelta(seconds=1))
    stamp = datetime.now(UTC).isoformat()
    hass.states.async_set(BUTTON, stamp, {"event_type": kind})
    await hass.async_block_till_done()


async def _turn(hass: HomeAssistant, to: float, context: Context | None = None) -> None:
    """The device reports a new volume, as it does after a click of its dial."""
    hass.states.async_set(OWN, "idle", {"volume_level": to}, context=context)
    await hass.async_block_till_done()


async def _rest(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _volume(hass: HomeAssistant) -> float:
    state = hass.states.get(OWN)
    assert state is not None
    level: float = state.attributes["volume_level"]
    return level


def _stations(played: list[ServiceCall]) -> list[str]:
    return [call.data["media_content_id"].rsplit("/", 1)[1] for call in played]


def test_a_change_of_volume_is_clicks_only_in_whole_steps() -> None:
    assert clicks_in(0.05) == 1
    assert clicks_in(-0.05) == -1
    assert clicks_in(0.2) == 4  # a flick arrives as one change of several steps
    assert clicks_in(0.0499999) == 1
    assert clicks_in(0.07) == 0  # somebody moved a slider
    assert clicks_in(0.0) == 0


async def test_the_dial_is_read_from_the_devices_own_player(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, schedule_text: str
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text)
    assert volume_player_for(hass, BUTTON, SPEAKER) == OWN
    assert volume_player_for(hass, "event.nowhere", SPEAKER) == SPEAKER


async def test_without_a_double_press_the_dial_is_volume(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
    played: list[ServiceCall],
    put_back: list[float],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text)

    await _turn(hass, 0.30)
    await _press(hass, freezer, "triple_press")  # another press does not arm it
    await _turn(hass, 0.35)
    await _rest(hass, freezer, 1)

    assert played == []
    assert put_back == []


async def test_after_a_double_press_each_click_is_a_station_and_the_volume_is_put_back(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
    played: list[ServiceCall],
    put_back: list[float],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text)
    await _press(hass, freezer, "double_press")

    await _turn(hass, 0.30)  # one click; the stand-in device then reports 0.25 again
    assert put_back == [HOME]
    assert played == []  # not until the dial rests
    await _rest(hass, freezer, REST_S)
    # Never tuned before, so the first click lands on the first station.
    assert _stations(played) == ["nature_01.flac"]

    await _turn(hass, 0.30)
    await _turn(hass, 0.30)  # two slow clicks, each put back in between
    await _rest(hass, freezer, REST_S)
    assert _stations(played)[1:] == ["learn_01.flac"]

    await _turn(hass, 0.20)  # and one back
    await _rest(hass, freezer, REST_S)
    assert _stations(played)[2:] == ["work_01.flac"]
    assert set(put_back) == {HOME}
    assert _volume(hass) == HOME


async def test_a_flick_is_counted_by_its_size_and_a_long_turn_stops_at_the_end_of_the_dial(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
    played: list[ServiceCall],
    put_back: list[float],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text)
    await hass.services.async_call(
        DOMAIN, "tune", {"entity_id": SPEAKER, "station": "Work"}, blocking=True
    )
    await _press(hass, freezer, "double_press")

    await _turn(hass, 0.40)  # three clicks in one update
    await _rest(hass, freezer, REST_S)
    assert _stations(played)[1:] == ["weird_01.flac"]

    await _turn(hass, 0.75)  # ten clicks: past the last station
    await _rest(hass, freezer, REST_S)
    assert _stations(played)[2:] == ["sleep_01.flac"]

    await _turn(hass, 0.30)  # already at the end: nothing to tune
    await _rest(hass, freezer, REST_S)
    assert len(played) == 3


async def test_the_dial_goes_back_to_volume_when_it_has_been_left_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
    played: list[ServiceCall],
    put_back: list[float],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text)
    await _press(hass, freezer, "double_press")
    await _turn(hass, 0.30)
    await _rest(hass, freezer, ARMED_S - 1)  # a click keeps it armed for ARMED_S more
    await _turn(hass, 0.30)
    await _rest(hass, freezer, ARMED_S + 1)
    assert len(played) == 2

    await _turn(hass, 0.30)  # now a plain volume change
    await _rest(hass, freezer, 1)
    assert len(played) == 2
    assert _volume(hass) == 0.30


async def test_a_volume_somebody_set_is_kept_and_is_not_a_click(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
    played: list[ServiceCall],
    put_back: list[float],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text)
    await _press(hass, freezer, "double_press")

    await _turn(hass, 0.30, Context(user_id="someone"))  # a whole step, but a person's
    await _turn(hass, 0.42)  # not a whole number of steps: a slider
    await _rest(hass, freezer, REST_S)
    assert played == []
    assert put_back == []

    await _turn(hass, 0.47)  # a click from there is put back to there
    await _rest(hass, freezer, REST_S)
    assert put_back == [0.42]
    assert len(played) == 1


async def test_a_button_coming_back_after_a_restart_does_not_arm_the_dial(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
    played: list[ServiceCall],
    put_back: list[float],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text)
    hass.states.async_set(BUTTON, "unavailable")
    await hass.async_block_till_done()
    await _press(hass, freezer, "double_press")  # its last press, restored

    await _turn(hass, 0.30)
    await _rest(hass, freezer, 1)
    assert played == []


async def test_a_speaker_given_no_button_keeps_its_volume_dial(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text: str,
    freezer: FrozenDateTimeFactory,
    played: list[ServiceCall],
    put_back: list[float],
) -> None:
    await _set_up(hass, aioclient_mock, schedule_text, dial=False)
    await _press(hass, freezer, "double_press")
    await _turn(hass, 0.30)
    await _rest(hass, freezer, 1)
    assert played == []
    assert put_back == []
