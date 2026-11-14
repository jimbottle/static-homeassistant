"""segments.py against the timeline: a stream cut to it never drifts from it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.static_radio.schedule import (
    BULLETIN_CAP_MS,
    GAP_MS,
    Schedule,
    Station,
    live,
    parse_schedule,
)
from custom_components.static_radio.segments import OPENING_MS, heard_at_ms, segment_at

FIXTURES = Path(__file__).parent / "fixtures"
RAW: dict[str, Any] = json.loads((FIXTURES / "schedule-wear.2026-11-12.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def schedule() -> Schedule:
    return parse_schedule(RAW)


def _stations(schedule: Schedule) -> list[Station]:
    return [station for station in schedule.stations if station.episodes]


def test_a_segment_from_a_slots_start_is_the_whole_slot(schedule: Schedule) -> None:
    for station in _stations(schedule):
        on_air = live(schedule, station, schedule.epoch_ms)
        assert on_air is not None
        start = schedule.epoch_ms - on_air.offset_ms
        segment = segment_at(schedule, station, start)
        assert segment is not None
        assert segment.start_ms == 0
        assert segment.audio_ms == on_air.audio_ms
        assert segment.length_ms == on_air.slot_ms
        assert segment.silence_ms >= GAP_MS


def test_tuning_in_mid_episode_sends_only_what_is_left(schedule: Schedule) -> None:
    station = _stations(schedule)[0]
    first = segment_at(schedule, station, schedule.epoch_ms)
    assert first is not None
    slot_start = first.at_ms - first.start_ms
    into = slot_start + 10_000
    segment = segment_at(schedule, station, into)
    assert segment is not None
    assert segment.start_ms == 10_000
    assert segment.start_ms + segment.audio_ms == first.episode.audio_ms
    assert segment.end_ms == slot_start + first.episode.slot_ms


def test_tuning_in_during_the_gap_sends_silence_to_the_next_episode(schedule: Schedule) -> None:
    station = _stations(schedule)[0]
    whole = segment_at(schedule, station, schedule.epoch_ms)
    assert whole is not None
    slot_start = whole.at_ms - whole.start_ms
    in_gap = slot_start + whole.episode.audio_ms + 400
    segment = segment_at(schedule, station, in_gap)
    assert segment is not None
    assert segment.audio_ms == 0
    assert segment.start_ms == 0
    assert segment.end_ms == slot_start + whole.episode.slot_ms


def test_a_bulletin_is_cut_at_its_cap_and_its_slot_keeps_its_length(schedule: Schedule) -> None:
    seen = 0
    for station in _stations(schedule):
        at = schedule.epoch_ms
        for _ in range(len(station.episodes)):
            segment = segment_at(schedule, station, at)
            assert segment is not None
            if segment.episode.is_bulletin and segment.start_ms == 0:
                seen += 1
                assert segment.audio_ms <= BULLETIN_CAP_MS
                assert segment.length_ms == segment.episode.duration_ms + GAP_MS
            at = segment.end_ms
    assert seen, "the fixture has bulletins; none were walked"


def test_segments_chained_for_a_day_stay_on_the_timeline(schedule: Schedule) -> None:
    """Follow each station for 26 hours from an awkward instant, slot by slot."""
    for station in _stations(schedule):
        start = schedule.epoch_ms + 7 * 3600 * 1000 + 123
        at = start
        previous = None
        while at < start + 26 * 3600 * 1000:
            segment = segment_at(schedule, station, at)
            assert segment is not None
            assert segment.length_ms > 0
            on_air = live(schedule, station, at)
            assert on_air is not None
            assert on_air.episode is segment.episode
            if previous is not None:
                # Every segment after the first starts a slot, on its first millisecond.
                assert on_air.offset_ms == 0
                assert segment.start_ms == 0
            # One millisecond before the segment ends, the same slot is still on.
            last = live(schedule, station, segment.end_ms - 1)
            assert last is not None
            assert last.slot_index == segment.slot_index
            previous = segment
            at = segment.end_ms


def test_before_the_epoch_works_like_any_other_time(schedule: Schedule) -> None:
    station = _stations(schedule)[0]
    segment = segment_at(schedule, station, schedule.epoch_ms - 5 * 3600 * 1000 - 77)
    assert segment is not None
    assert segment.length_ms > 0


def test_the_next_millisecond_is_heard_a_lead_after_it_is_sent() -> None:
    assert heard_at_ms(1_000_000, 1300, 0) == 1_001_300
    assert heard_at_ms(1_000_000, 1300, OPENING_MS) == 1_001_300 + OPENING_MS


def test_the_opening_passes_the_three_seconds_a_player_waits_for() -> None:
    assert OPENING_MS > 3000
