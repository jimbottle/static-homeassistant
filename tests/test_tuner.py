"""tuner.py: stations by Static's own names, steps along the dial, and what is said.

The owner's two rules are the point of these tests: a station answers only to
the name Static gives it, and every change of station is announced with that
name exactly as Static writes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.static_radio.schedule import Schedule, parse_schedule
from custom_components.static_radio.tuner import (
    end_of_dial_reply,
    find_station,
    step,
    tuned_reply,
    unknown_station_reply,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DIAL = ["Nature", "Work", "Learn", "News", "Weird", "Unite", "Make", "SHEILA B", "Sleep"]


@pytest.fixture(scope="module", params=["2026-11-11", "2026-11-12"])
def schedule(request: pytest.FixtureRequest) -> Schedule:
    text = (FIXTURES / f"schedule-wear.{request.param}.json").read_text(encoding="utf-8")
    return parse_schedule(json.loads(text))


def test_the_dial_is_statics_in_statics_order(schedule: Schedule) -> None:
    assert [s.display_name for s in schedule.stations] == DIAL


def test_a_station_answers_to_each_of_statics_names_for_it(schedule: Schedule) -> None:
    for station in schedule.stations:
        for name in (station.display_name, station.u_tag, station.station_id):
            assert find_station(schedule.stations, name) is station
            assert find_station(schedule.stations, f"  {name.lower()} ") is station


@pytest.mark.parametrize(
    "said", ["", "Jazz", "the news", "Learning", "United", "Sheila", "Sheila Bee", "news station"]
)
def test_a_station_answers_to_no_other_name(schedule: Schedule, said: str) -> None:
    assert find_station(schedule.stations, said) is None


def test_every_change_of_station_is_announced_with_statics_name(schedule: Schedule) -> None:
    said = [tuned_reply(station) for station in schedule.stations]
    assert said == [f"Playing Static {name}." for name in DIAL]
    assert "Playing Static SHEILA B." in said  # as Static writes it, capitals and all


def test_stepping_goes_along_the_dial_and_stops_at_each_end(schedule: Schedule) -> None:
    stations = schedule.stations
    walked = [stations[0].display_name]
    current = stations[0]
    while (following := step(stations, current.station_id, +1)) is not None:
        walked.append(following.display_name)
        current = following
    assert walked == DIAL
    assert end_of_dial_reply(current, +1) == "Static Sleep is the last station on the dial."

    back = step(stations, current.station_id, -1)
    assert back is not None
    assert back.display_name == "SHEILA B"
    assert step(stations, stations[0].station_id, -1) is None
    assert end_of_dial_reply(stations[0], -1) == "Static Nature is the first station on the dial."


def test_a_step_from_nowhere_lands_on_the_first_station(schedule: Schedule) -> None:
    first = schedule.stations[0]
    assert step(schedule.stations, None, +1) is first
    assert step(schedule.stations, "gone_01", -1) is first
    assert step((), None, +1) is None


def test_an_unknown_name_is_answered_with_the_names_that_exist(schedule: Schedule) -> None:
    assert unknown_station_reply(schedule.stations) == (
        "Static's stations are Nature, Work, Learn, News, Weird, Unite, Make, SHEILA B and Sleep."
    )
    assert unknown_station_reply(schedule.stations[:1]) == "Static's stations are Nature."
    assert unknown_station_reply(()) == "Static has no stations on the dial right now."
