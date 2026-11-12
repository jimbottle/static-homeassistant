"""The Configure screen, the station selector it creates, and sending a Support link.

No audio anywhere: choosing a station keeps the choice and plays nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
    mock_restore_cache,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import SCHEDULE_URL, Response
from custom_components.static_radio.const import (
    CONF_LEADS_MS,
    CONF_NOTIFY_SERVICE,
    CONF_SPEAKERS,
    DOMAIN,
    TITLE,
)
from custom_components.static_radio.intents import INTENT_SEND_SUPPORT_LINK, spoken_link
from custom_components.static_radio.schedule import Episode, live, parse_schedule

START = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)  # inside the 2026-11-12 schedule's day
SPEAKER = "media_player.kitchen_speaker"
OTHER = "media_player.office_speaker"
DIAL = ["Nature", "Work", "Learn", "News", "Weird", "Unite", "Make", "SHEILA B", "Sleep"]


@pytest.fixture(autouse=True)
def _no_feeds() -> Iterator[None]:
    async def fetch(_url: str, _headers: Mapping[str, str]) -> Response:
        return Response(404, {}, b"")

    with patch("custom_components.static_radio.feed.feed_fetch", lambda _hass, _session: fetch):
        yield


@pytest.fixture(autouse=True)
def _inside_the_day(freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(START)


def _entry(hass: HomeAssistant, options: dict[str, object]) -> MockConfigEntry:
    config_entry = MockConfigEntry(domain=DOMAIN, title=TITLE, data={}, options=options)
    config_entry.add_to_hass(hass)
    return config_entry


async def _set_up(
    hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker, text: str
) -> None:
    aioclient_mock.get(SCHEDULE_URL, text=text)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


def _select_id(hass: HomeAssistant, entry: MockConfigEntry, speaker: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN, f"{entry.entry_id}_{speaker}_station"
    )
    assert entity_id is not None
    return entity_id


# -- the Configure screen ------------------------------------------------------


async def test_configure_takes_speakers_a_lead_for_each_and_a_phone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
) -> None:
    async_mock_service(hass, "notify", "mobile_app_phone")
    entry = _entry(hass, {})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    first = await hass.config_entries.options.async_init(entry.entry_id)
    assert first["type"] is FlowResultType.FORM
    assert first["step_id"] == "init"

    second = await hass.config_entries.options.async_configure(
        first["flow_id"],
        {CONF_SPEAKERS: [SPEAKER, OTHER], CONF_NOTIFY_SERVICE: "mobile_app_phone"},
    )
    assert second["type"] is FlowResultType.FORM
    assert second["step_id"] == "leads"
    # Each speaker starts from the reference player's 1.0 s.
    leads_form = second["data_schema"]
    assert leads_form is not None
    defaults = {str(key): key.default() for key in leads_form.schema}
    assert defaults == {SPEAKER: 1000, OTHER: 1000}

    done = await hass.config_entries.options.async_configure(
        second["flow_id"], {SPEAKER: 1200, OTHER: 850}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SPEAKERS: [SPEAKER, OTHER],
        CONF_NOTIFY_SERVICE: "mobile_app_phone",
        CONF_LEADS_MS: {SPEAKER: 1200, OTHER: 850},
    }
    # Saving reloads the entry, and each speaker now has its selector.
    assert hass.states.get(_select_id(hass, entry, SPEAKER)) is not None
    assert hass.states.get(_select_id(hass, entry, OTHER)) is not None
    assert aioclient_mock.call_count == 1  # the reload did not ask Static again


async def test_only_phones_are_offered_and_a_removed_speaker_loses_its_selector(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
) -> None:
    for action in ("mobile_app_phone", "send_message", "persistent_notification"):
        async_mock_service(hass, "notify", action)
    entry = _entry(hass, {CONF_SPEAKERS: [SPEAKER, OTHER], CONF_LEADS_MS: {}})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)
    kept, removed = _select_id(hass, entry, SPEAKER), _select_id(hass, entry, OTHER)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": kept, "option": "News"}, blocking=True
    )

    first = await hass.config_entries.options.async_init(entry.entry_id)
    form = first["data_schema"]
    assert form is not None
    offered = form.schema[CONF_NOTIFY_SERVICE].config["options"]
    assert offered == ["none", "mobile_app_phone"]

    second = await hass.config_entries.options.async_configure(
        first["flow_id"], {CONF_SPEAKERS: [SPEAKER], CONF_NOTIFY_SERVICE: "none"}
    )
    await hass.config_entries.options.async_configure(second["flow_id"], {SPEAKER: 1000})
    await hass.async_block_till_done(wait_background_tasks=True)

    registry = er.async_get(hass)
    assert registry.async_get(removed) is None  # no orphan left on the device
    assert registry.async_get(kept) is not None
    still = hass.states.get(kept)
    assert still is not None
    assert still.state == "News"  # the speaker that stayed kept its station


async def test_configure_with_no_speakers_and_no_phone_saves_nothing_to_tune(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
) -> None:
    entry = _entry(hass, {CONF_SPEAKERS: [SPEAKER], CONF_LEADS_MS: {SPEAKER: 900}})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    first = await hass.config_entries.options.async_init(entry.entry_id)
    done = await hass.config_entries.options.async_configure(
        first["flow_id"], {CONF_SPEAKERS: [], CONF_NOTIFY_SERVICE: "none"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SPEAKERS: [], CONF_LEADS_MS: {}}


async def test_a_lead_outside_the_range_is_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
) -> None:
    entry = _entry(hass, {})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)
    first = await hass.config_entries.options.async_init(entry.entry_id)
    second = await hass.config_entries.options.async_configure(
        first["flow_id"], {CONF_SPEAKERS: [SPEAKER], CONF_NOTIFY_SERVICE: "none"}
    )
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(second["flow_id"], {SPEAKER: 60_000})


# -- the station selector ------------------------------------------------------


async def test_the_selector_lists_statics_stations_and_keeps_the_choice(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
) -> None:
    hass.states.async_set(SPEAKER, "idle", {"friendly_name": "Kitchen speaker"})
    entry = _entry(hass, {CONF_SPEAKERS: [SPEAKER]})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)
    entity_id = _select_id(hass, entry, SPEAKER)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["options"] == DIAL  # Static's names, in dial order
    assert state.attributes["speaker"] == SPEAKER
    assert state.name == f"{TITLE} Kitchen speaker station"
    assert state.state == "unknown"  # nothing chosen yet

    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": "SHEILA B"}, blocking=True
    )
    chosen = hass.states.get(entity_id)
    assert chosen is not None
    assert chosen.state == "SHEILA B"

    # Choosing a station asks Static for nothing and touches no audio address.
    assert aioclient_mock.call_count == 1
    audio = {
        e["audioUrl"]
        for c in json.loads(schedule_text_with_links)["categories"].values()
        for e in c["episodes"]
    }
    assert not any(str(url) in audio for _m, url, _d, _h in aioclient_mock.mock_calls)


async def test_a_name_that_is_not_statics_is_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
) -> None:
    entry = _entry(hass, {CONF_SPEAKERS: [SPEAKER]})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)
    entity_id = _select_id(hass, entry, SPEAKER)

    with pytest.raises(Exception, match="not"):
        await hass.services.async_call(
            "select", "select_option", {"entity_id": entity_id, "option": "Jazz"}, blocking=True
        )
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unknown"


async def test_the_last_station_is_remembered_across_a_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
) -> None:
    entry = _entry(hass, {CONF_SPEAKERS: [SPEAKER, OTHER]})
    # What Home Assistant saved before the restart. The entity ids are the ones
    # the registry will hand out again.
    registry = er.async_get(hass)
    kept = registry.async_get_or_create(
        "select", DOMAIN, f"{entry.entry_id}_{SPEAKER}_station", config_entry=entry
    )
    gone = registry.async_get_or_create(
        "select", DOMAIN, f"{entry.entry_id}_{OTHER}_station", config_entry=entry
    )
    mock_restore_cache(hass, [State(kept.entity_id, "Weird"), State(gone.entity_id, "Jazz")])

    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    remembered = hass.states.get(kept.entity_id)
    assert remembered is not None
    assert remembered.state == "Weird"
    # A station that has left the dial is not come back to.
    dropped = hass.states.get(gone.entity_id)
    assert dropped is not None
    assert dropped.state == "unknown"


# -- sending the Support link --------------------------------------------------


def _find(text: str, kind: str) -> tuple[datetime, str, Episode]:
    schedule = parse_schedule(json.loads(text))
    for minute in range(0, 20 * 60, 3):
        when = START + timedelta(minutes=minute)
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
                return when, station.display_name, episode
    raise AssertionError(f"the saved schedule never has: {kind}")


async def _send(hass: HomeAssistant, station: str) -> str:
    response = await intent.async_handle(
        hass, "test", INTENT_SEND_SUPPORT_LINK, {"station": {"value": station}}
    )
    said: str = response.speech["plain"]["speech"]
    assert said.endswith(".")
    assert "?" not in said
    return said


async def test_the_link_is_sent_to_the_chosen_phone_and_opens_the_support_page(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, station, episode = _find(schedule_text_with_links, "link")
    freezer.move_to(when)
    sent: list[ServiceCall] = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = _entry(hass, {CONF_NOTIFY_SERVICE: "mobile_app_phone"})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    said = await _send(hass, station)

    assert said.startswith("I sent the Support link for ")
    assert said.endswith(" to your phone.")
    assert len(sent) == 1
    assert sent[0].data["message"] == f"Support: {episode.support_url}"  # labeled Support
    assert sent[0].data["title"].startswith("Support ")
    assert sent[0].data["data"] == {
        "clickAction": episode.support_url,
        "url": episode.support_url,
    }


async def test_with_no_phone_chosen_the_link_is_read_out(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, station, episode = _find(schedule_text_with_links, "link")
    assert episode.support_url is not None
    freezer.move_to(when)
    sent = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = _entry(hass, {})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    said = await _send(hass, station)

    assert said.startswith("No phone is set up for Static links, but you can support ")
    assert spoken_link(episode.support_url) in said
    assert sent == []


async def test_a_phone_that_cannot_be_reached_still_gets_the_listener_the_link(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    when, station, episode = _find(schedule_text_with_links, "link")
    assert episode.support_url is not None
    freezer.move_to(when)

    async def unreachable(_call: ServiceCall) -> None:
        raise HomeAssistantError("device not reachable")

    hass.services.async_register("notify", "mobile_app_phone", unreachable)
    entry = _entry(hass, {CONF_NOTIFY_SERVICE: "mobile_app_phone"})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    said = await _send(hass, station)

    assert said.startswith("I could not send it to your phone, but you can support ")
    assert spoken_link(episode.support_url) in said


@pytest.mark.parametrize("kind", ["no link", "bulletin"])
async def test_nothing_is_sent_for_a_bulletin_or_a_show_without_a_link(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    schedule_text_with_links: str,
    freezer: FrozenDateTimeFactory,
    kind: str,
) -> None:
    when, station, _episode = _find(schedule_text_with_links, kind)
    freezer.move_to(when)
    sent = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = _entry(hass, {CONF_NOTIFY_SERVICE: "mobile_app_phone"})
    await _set_up(hass, entry, aioclient_mock, schedule_text_with_links)

    said = await _send(hass, station)

    assert "no Support link." in said
    assert sent == []
