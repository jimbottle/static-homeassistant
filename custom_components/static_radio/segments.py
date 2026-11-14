"""What a station's stream holds, millisecond by millisecond.

A station is one endless stream. This file says what goes into it and in what
order, from the moment a speaker tunes in: how far into which episode to
start, how much of its audio to send, and how much silence follows before the
next one. station_audio.py makes the sound; this file does the sums,
so they can be checked against Static's own timeline without a speaker.

The stream is cut to the timeline exactly. Each piece is as long as the slot
it fills, never as long as the file happened to be, so the stream's own clock
and Static's stay together for as long as a speaker keeps listening. A file
that runs short is padded and one that runs long is cut (station_audio.py).

Two things are added at the start of a listen:

- The lead. A speaker makes its first sound some time after it is handed a
  stream (measured, per speaker). The stream starts that far ahead of now, so
  what is heard is what is on the air when it is heard.
- An opening. An episode comes from the show's own host, and how long that
  takes to answer is not ours to know. A player also waits until it holds a
  few seconds of sound before it makes any (Music Assistant: 3 s). So the
  stream opens with a stretch the player can start on at once, long enough to
  pass that mark by itself: the station's name, spoken, and silence. The
  episode joins where it should be by then. A slow host makes a longer
  silence and never a late stream.

No I/O here and nothing from Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schedule import Episode, Schedule, Station, live

# The least the stream opens with before an episode: past the 3 s a player waits for.
OPENING_MS = 3200


@dataclass(frozen=True, slots=True)
class Segment:
    """One slot's share of the stream: some of an episode, then silence."""

    episode: Episode
    slot_index: int  # the episode's place in the station's loop
    at_ms: int  # the instant on Static's timeline this segment starts at
    start_ms: int  # how far into the episode's audio to start; 0 unless tuning in
    audio_ms: int  # how much audio to send from there; 0 when tuning in during a gap
    silence_ms: int  # silence after the audio, up to the next episode

    @property
    def length_ms(self) -> int:
        """How long this segment holds the stream."""
        return self.audio_ms + self.silence_ms

    @property
    def end_ms(self) -> int:
        """The instant the next segment starts at: always a slot's first millisecond."""
        return self.at_ms + self.length_ms


def segment_at(schedule: Schedule, station: Station, at_ms: int) -> Segment | None:
    """The rest of the slot that is on the air at at_ms.

    Call it again with the answer's end_ms for the segment after it: that
    lands on the next slot's first millisecond, around the loop for ever.
    None for a station with nothing on it.
    """
    on_air = live(schedule, station, at_ms)
    if on_air is None:
        return None
    audio_ms = max(0, on_air.audio_ms - on_air.offset_ms)
    return Segment(
        episode=on_air.episode,
        slot_index=on_air.slot_index,
        at_ms=at_ms,
        # In the gap there is no audio left to start in the middle of.
        start_ms=on_air.offset_ms if audio_ms else 0,
        audio_ms=audio_ms,
        silence_ms=on_air.slot_ms - max(on_air.offset_ms, on_air.audio_ms),
    )


def heard_at_ms(connected_ms: int, lead_ms: int, sent_ms: int) -> int:
    """The instant a listener hears the stream's next millisecond.

    The stream's first sample is heard lead_ms after the speaker connected,
    and everything after it follows a millisecond for a millisecond: sent_ms
    of stream later. This is the instant to ask the timeline about.
    """
    return connected_ms + lead_ms + sent_ms
