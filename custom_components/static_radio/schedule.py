"""The day's schedule and the live-position rules.

Every Static station is a loop of episodes that started at the schedule's
daily epoch, with 1.5 s of silence after each episode. What is live is
arithmetic on the clock and that one public file, so everyone who tunes in at
the same moment lands on the same moment.

A port of Schedule.pm from Static's reference player, static-squeezebox
(https://github.com/madewith-bestpractice/static-squeezebox), checked against
it and against the liveNow() function in Static's developer guide
(https://staticradio.app/developers#whats-live). Fetching lives in api.py; this
file is the parsing and the math, and imports nothing from Home Assistant.

All times are integer milliseconds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 8  # the schedule format this was written against

GAP_MS = 1500  # silence after every episode
BULLETIN_CAP_MS = 30_000  # bulletins never play past 30 s, even if the file runs longer

# A schedule whose epoch is further than this from now belongs to another
# broadcast day and must not be played (Schedule.pm rejects it the same way).
WRONG_DAY_MS = 30 * 3600 * 1000

_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Stations without a sort_index go to the end of the dial, as in Schedule.pm.
_UNSORTED = 999


class ScheduleError(ValueError):
    """The schedule file cannot be used."""


@dataclass(frozen=True, slots=True)
class Episode:
    """One entry in a station's loop: a show's episode or a community bulletin."""

    episode_id: str
    audio_url: str  # on the show's own host; always request this, never its redirect target
    duration_ms: int  # how long it holds the air; its slot is this plus GAP_MS
    title: str
    feed_title: str  # the show's name
    episode_url: str  # the episode's page, or a bulletin's link; may be empty
    feed_url: str  # the show's RSS feed; empty for bulletins
    image_url: str  # may be a relative path (not https): use a fallback then
    gain_db: float  # loudness correction, EBU R128
    is_bulletin: bool
    support_url: str | None  # the show's own support page, when it has one
    placement_id: str | None  # bulletins only: the break it fills

    @property
    def slot_ms(self) -> int:
        """The time this episode occupies on the timeline, silence included."""
        return self.duration_ms + GAP_MS

    @property
    def audio_ms(self) -> int:
        """Where the audio stops: the duration, or the cap for a bulletin."""
        if self.is_bulletin:
            return min(self.duration_ms, BULLETIN_CAP_MS)
        return self.duration_ms


@dataclass(frozen=True, slots=True)
class Station:
    """One station: a loop of episodes in airing order."""

    station_id: str
    display_name: str
    u_tag: str  # the name as it appears on the dial, in capitals
    sort_index: int  # position on the dial, from 0
    loop_ms: int  # loop_duration_seconds, without the gaps
    episodes: tuple[Episode, ...]  # episodes with no duration already left out

    @property
    def pad_ms(self) -> int:
        """All the gaps in one loop."""
        return len(self.episodes) * GAP_MS

    @property
    def sum_ms(self) -> int:
        """The episodes' durations added up, without the gaps."""
        return sum(e.duration_ms for e in self.episodes)


@dataclass(frozen=True, slots=True)
class Schedule:
    """Every station's loop for one broadcast day."""

    schema_version: int
    epoch_ms: int  # when the loops started; often in the future
    generated_at_utc: str
    stations: tuple[Station, ...]  # in dial order

    def station(self, station_id: str) -> Station | None:
        """The station with this id, if it is on the dial."""
        for station in self.stations:
            if station.station_id == station_id:
                return station
        return None

    def is_current_day(self, now_ms: int) -> bool:
        """False for another broadcast day's timeline, which must not be played."""
        return abs(now_ms - self.epoch_ms) < WRONG_DAY_MS


@dataclass(frozen=True, slots=True)
class Live:
    """What is on a station at one instant."""

    episode: Episode
    slot_index: int  # the episode's place in the station's loop
    offset_ms: int  # how far into the slot the instant is: start the audio here
    slot_start_ms: int  # when this episode started
    slot_ms: int  # the slot's length, silence included
    audio_ms: int  # stop the audio this far into the slot

    @property
    def in_gap(self) -> bool:
        """Past the audio: stay quiet until the next episode."""
        return self.offset_ms >= self.audio_ms

    @property
    def next_at_ms(self) -> int:
        """When the next episode starts, whether or not this file has finished."""
        return self.slot_start_ms + self.slot_ms


def parse_epoch_ms(value: str) -> int:
    """An ISO 8601 instant as milliseconds since the Unix epoch.

    A value without an offset is read as UTC, which is what the field's name
    promises.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as err:
        raise ScheduleError(f"daily_epoch_utc is not a timestamp: {value!r}") from err
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    # Whole-number arithmetic: going through a float can land 1 ms short when
    # the value carries a fraction of a second.
    return (parsed - _UNIX_EPOCH) // timedelta(milliseconds=1)


def _episode(raw: dict[str, Any]) -> Episode:
    support = raw.get("supportUrl")
    placement = raw.get("placementId")
    return Episode(
        episode_id=str(raw.get("episodeId", "")),
        audio_url=str(raw.get("audioUrl", "")),
        duration_ms=int(raw.get("durationInSeconds") or 0) * 1000,
        title=str(raw.get("title", "")),
        feed_title=str(raw.get("feedTitle", "")),
        episode_url=str(raw.get("episodeUrl", "")),
        feed_url=str(raw.get("feedUrl", "")),
        image_url=str(raw.get("imageUrl", "")),
        gain_db=float(raw.get("gainDb") or 0.0),
        is_bulletin=bool(raw.get("isBulletin", False)),
        support_url=str(support) if support else None,
        placement_id=str(placement) if placement else None,
    )


def parse_schedule(raw: dict[str, Any]) -> Schedule:
    """Build a Schedule from the decoded schedule-wear.json.

    Stations are read from the file, never hard-coded, and ordered by
    sort_index. Fields this code does not know are ignored, because new ones
    can appear without a version change. Raises ScheduleError when there is
    nothing playable in the file.
    """
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        _LOGGER.warning(
            "schedule schema_version is %r, not %d; check https://staticradio.app/developers",
            version,
            SCHEMA_VERSION,
        )

    epoch_ms = parse_epoch_ms(raw.get("daily_epoch_utc", ""))

    stations: list[Station] = []
    categories = raw.get("categories") or {}
    for station_id, cat in categories.items():
        if not cat.get("is_active", True):
            continue
        # An episode with no duration holds no time on the air: leave it out
        # before any arithmetic, so the gap count matches every other player's.
        episodes = tuple(
            e for e in (_episode(r) for r in cat.get("episodes") or []) if e.duration_ms > 0
        )
        loop_ms = int(cat.get("loop_duration_seconds") or 0) * 1000
        if not episodes or loop_ms <= 0:
            continue
        display_name = str(cat.get("display_name") or cat.get("u_tag") or station_id)
        sort_index = cat.get("sort_index")
        stations.append(
            Station(
                station_id=str(station_id),
                display_name=display_name,
                u_tag=str(cat.get("u_tag") or display_name.upper()),
                sort_index=_UNSORTED if sort_index is None else int(sort_index),
                loop_ms=loop_ms,
                episodes=episodes,
            )
        )

    if not stations:
        raise ScheduleError("the schedule has no playable stations")
    stations.sort(key=lambda s: (s.sort_index, s.station_id))

    return Schedule(
        schema_version=int(version) if isinstance(version, int) else 0,
        epoch_ms=epoch_ms,
        generated_at_utc=str(raw.get("generated_at_utc", "")),
        stations=tuple(stations),
    )


def live(schedule: Schedule, station: Station, now_ms: int) -> Live | None:
    """The episode on a station at now_ms, and how far into it that is.

    Compute it for the moment sound will actually start, not the moment of
    the request. startOffsetSeconds is never used: it leaves out the gaps, so
    it drifts 1.5 s further off with every episode.
    """
    loop_ms = station.loop_ms + station.pad_ms
    total_ms = station.sum_ms + station.pad_ms

    # The epoch is often in the future, so now - epoch is often negative.
    # Python's % takes the sign of the divisor and never goes negative here.
    pos = (now_ms - schedule.epoch_ms) % loop_ms
    # loop_duration_seconds is the sum of the durations, so this changes
    # nothing in a well-formed file. The reference does it, so a file where
    # the two disagree is read the same way everywhere.
    pos %= total_ms

    start = 0
    for index, episode in enumerate(station.episodes):
        if pos < start + episode.slot_ms:
            offset_ms = pos - start
            return Live(
                episode=episode,
                slot_index=index,
                offset_ms=offset_ms,
                slot_start_ms=now_ms - offset_ms,
                slot_ms=episode.slot_ms,
                audio_ms=episode.audio_ms,
            )
        start += episode.slot_ms
    return None
