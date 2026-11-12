"""schedule.py against Static's own answers, on a saved copy of the schedule.

live-expected.*.json holds what the reference player (t/live_test.pl in
static-squeezebox) and the developer guide's liveNow() gave for every station
at instants chosen to hit the edges: before the epoch, inside a gap, past a
bulletin's 30 s and across a loop wrap. tools/make_live_expected.py writes it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from custom_components.static_radio.schedule import (
    BULLETIN_CAP_MS,
    GAP_MS,
    WRONG_DAY_MS,
    Schedule,
    ScheduleError,
    live,
    parse_epoch_ms,
    parse_schedule,
)

FIXTURES = Path(__file__).parent / "fixtures"
DAY = "2026-11-11"
RAW: dict[str, Any] = json.loads((FIXTURES / f"schedule-wear.{DAY}.json").read_text("utf-8"))
EXPECTED: list[dict[str, Any]] = json.loads(
    (FIXTURES / f"live-expected.{DAY}.json").read_text("utf-8")
)
SCHEMA = json.loads((FIXTURES / "schedule-wear.v8.schema.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def schedule() -> Schedule:
    return parse_schedule(RAW)


def test_fixture_matches_the_published_schema() -> None:
    jsonschema.validate(RAW, SCHEMA)


def test_stations_come_from_the_file_in_dial_order(schedule: Schedule) -> None:
    by_dial = sorted(RAW["categories"].values(), key=lambda c: c["sort_index"])
    assert [s.station_id for s in schedule.stations] == [c["id"] for c in by_dial]
    assert [s.sort_index for s in schedule.stations] == list(range(len(by_dial)))


def test_every_station_is_checked_at_five_or_more_instants(schedule: Schedule) -> None:
    for station in schedule.stations:
        cases = [row for row in EXPECTED if row["station"] == station.station_id]
        assert len(cases) >= 5, station.station_id
        names = {row["case"] for row in cases}
        assert {"before the epoch", "inside the first 1.5 s gap", "at the loop wrap"} <= names
    assert any(row["case"] == "past 30 s of a bulletin" for row in EXPECTED)


@pytest.mark.parametrize("row", EXPECTED, ids=lambda r: f"{r['station']}: {r['case']}")
def test_live_matches_the_reference_and_the_guide(schedule: Schedule, row: dict[str, Any]) -> None:
    station = schedule.station(row["station"])
    assert station is not None
    got = live(schedule, station, row["t"])
    assert got is not None

    # The guide's liveNow(), to the millisecond.
    js = row["js"]
    assert got.episode.episode_id == js["episodeId"]
    assert got.episode.audio_url == js["audioUrl"]
    assert got.offset_ms == js["offsetMs"]
    assert got.in_gap == js["inGap"]
    assert got.audio_ms == js["audioMs"]
    assert got.slot_start_ms == js["startedAtMs"]
    assert got.next_at_ms == js["nextAtMs"]

    # The reference player, which prints whole seconds.
    perl = row["perl"]
    assert got.episode.audio_url == perl["audioUrl"]
    assert got.offset_ms // 1000 == perl["offsetS"]
    assert int(got.episode.is_bulletin) == perl["bulletin"]


def test_a_bulletin_stops_at_thirty_seconds_even_if_its_file_is_longer() -> None:
    raw = _tiny(
        [{"durationInSeconds": 45, "isBulletin": True}, {"durationInSeconds": 60}],
        loop=105,
    )
    sched = parse_schedule(raw)
    station = sched.stations[0]
    at = live(sched, station, sched.epoch_ms + 31_000)
    assert at is not None
    assert at.audio_ms == BULLETIN_CAP_MS
    assert at.in_gap  # past the cap: silence, though the slot runs to 46.5 s
    assert at.slot_ms == 45_000 + GAP_MS


def test_episodes_with_no_duration_are_left_out_before_the_arithmetic() -> None:
    raw = _tiny(
        [{"durationInSeconds": 10}, {"durationInSeconds": 0}, {"durationInSeconds": 20}],
        loop=30,
    )
    sched = parse_schedule(raw)
    station = sched.stations[0]
    assert len(station.episodes) == 2
    # Two gaps in the loop, not three: the third slot starts at 10 s + 1.5 s.
    at = live(sched, station, sched.epoch_ms + 11_500)
    assert at is not None
    assert at.slot_index == 1
    assert at.offset_ms == 0
    assert station.loop_ms + station.pad_ms == 33_000


def test_start_offset_seconds_is_ignored() -> None:
    eps = [
        {"durationInSeconds": 10, "startOffsetSeconds": 0},
        {"durationInSeconds": 10, "startOffsetSeconds": 10},
        {"durationInSeconds": 10, "startOffsetSeconds": 20},
    ]
    sched = parse_schedule(_tiny(eps, loop=30))
    # startOffsetSeconds says the third episode starts at 20 s. With the two
    # gaps before it, it starts at 23 s, so 22 s is still the second slot.
    at = live(sched, sched.stations[0], sched.epoch_ms + 22_000)
    assert at is not None
    assert at.slot_index == 1
    assert at.in_gap


def test_position_is_never_negative_before_the_epoch(schedule: Schedule) -> None:
    for station in schedule.stations:
        at = live(schedule, station, schedule.epoch_ms - 1)
        assert at is not None
        assert at.slot_index == len(station.episodes) - 1
        assert at.offset_ms == at.slot_ms - 1


def test_inactive_and_empty_stations_are_left_off_the_dial() -> None:
    raw = _tiny([{"durationInSeconds": 10}], loop=10)
    raw["categories"]["off_01"] = {
        **raw["categories"]["one_01"],
        "id": "off_01",
        "is_active": False,
    }
    raw["categories"]["empty_01"] = {
        **raw["categories"]["one_01"],
        "id": "empty_01",
        "episodes": [],
    }
    assert [s.station_id for s in parse_schedule(raw).stations] == ["one_01"]


def test_a_schedule_with_nothing_playable_is_refused() -> None:
    raw = _tiny([{"durationInSeconds": 0}], loop=10)
    with pytest.raises(ScheduleError):
        parse_schedule(raw)


def test_unknown_fields_are_ignored() -> None:
    raw = _tiny([{"durationInSeconds": 10, "somethingNew": {"a": 1}}], loop=10)
    raw["a_new_top_level_field"] = True
    raw["categories"]["one_01"]["a_new_station_field"] = [1, 2]
    assert parse_schedule(raw).stations[0].episodes[0].duration_ms == 10_000


def test_another_schema_version_warns_and_still_parses(caplog: pytest.LogCaptureFixture) -> None:
    raw = _tiny([{"durationInSeconds": 10}], loop=10)
    raw["schema_version"] = 9
    with caplog.at_level(logging.WARNING):
        parse_schedule(raw)
    assert "schema_version" in caplog.text


def test_another_days_schedule_is_not_current(schedule: Schedule) -> None:
    assert schedule.is_current_day(schedule.epoch_ms - 12 * 3600 * 1000)
    assert schedule.is_current_day(schedule.epoch_ms + WRONG_DAY_MS - 1)
    assert not schedule.is_current_day(schedule.epoch_ms + WRONG_DAY_MS)
    assert not schedule.is_current_day(schedule.epoch_ms - WRONG_DAY_MS)


def test_the_epoch_is_read_with_its_offset() -> None:
    assert parse_epoch_ms("2026-11-12T06:00:00+00:00") == 1_794_463_200_000
    assert parse_epoch_ms("2026-11-12T00:00:00-06:00") == 1_794_463_200_000
    assert parse_epoch_ms("2026-11-12T06:00:00") == 1_794_463_200_000
    assert parse_epoch_ms("2026-11-12T06:00:00.123+00:00") == 1_794_463_200_123
    with pytest.raises(ScheduleError):
        parse_epoch_ms("tomorrow")


def test_support_link_is_absent_when_the_show_has_none() -> None:
    eps: list[dict[str, Any]] = [
        {"durationInSeconds": 10, "supportUrl": "https://example.com/support"},
        {"durationInSeconds": 10},
    ]
    first, second = parse_schedule(_tiny(eps, loop=20)).stations[0].episodes
    assert first.support_url == "https://example.com/support"
    assert second.support_url is None


def _tiny(episodes: list[dict[str, Any]], loop: int) -> dict[str, Any]:
    """A one-station schedule small enough to check by hand."""
    return {
        "schema_version": 8,
        "daily_epoch_utc": "2026-11-12T06:00:00+00:00",
        "generated_at_utc": "2026-11-11T13:00:00+00:00",
        "categories": {
            "one_01": {
                "id": "one_01",
                "display_name": "One",
                "u_tag": "ONE",
                "is_active": True,
                "sort_index": 0,
                "loop_duration_seconds": loop,
                "episodes": [
                    {
                        "episodeId": f"e{i}",
                        "audioUrl": f"https://example.com/{i}.mp3",
                        "isBulletin": False,
                        **e,
                    }
                    for i, e in enumerate(episodes)
                ],
            }
        },
    }
