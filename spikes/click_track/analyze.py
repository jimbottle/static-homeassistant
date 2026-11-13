"""Read a recording of the click track and say how far off the clock it is.

    uv run python spikes/click_track/analyze.py recording.wav --started-at 1794450000.250

--started-at is the wall-clock time (epoch seconds) of the recording's first
sample, by the same clock that timed the server. Prints one line per mark heard
and a summary: the offset of what was heard from the true second, positive
when the speaker is late.

The ten-second and minute beeps give the whole seconds (unambiguous to +/- 5 s
and +/- 30 s); the clicks between them give the spread.
"""

from __future__ import annotations

import argparse
import math
import operator
import statistics
import sys
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import clicks  # noqa: E402

WINDOW_S = 0.004  # one look every 4 ms: four cycles of a click, eight of a beep
GAP_WINDOWS = 4  # a mark may drop out this long (a lost packet is 11 ms) and still be one
FLOOR = 0.001  # of full scale: a tone quieter than this is not a mark
OVER_ROOM = 10  # and it must be this many times the room's usual level at that pitch
ECHO_S = 0.3  # a mark this soon after another is the room still ringing
BODY = 0.3  # a mark is the part of a stretch at least this share of its peak
PURE = 0.6  # share of a window's loudness that must be the tone itself
CLICK_HZ, BEEP_HZ = 1000.0, 2000.0
CLICK_HEARD_MAX_S = 0.032  # the longest a heard click runs, once its ringing is left off


def _tone_levels(samples: array[int], rate: int, pitch: float) -> list[tuple[float, float]]:
    """(tone amplitude, overall loudness) of each window, both 0..1 of full scale."""
    win = max(1, int(rate * WINDOW_S))
    cos = [math.cos(2 * math.pi * pitch * i / rate) for i in range(win)]
    sin = [math.sin(2 * math.pi * pitch * i / rate) for i in range(win)]
    out = []
    for at in range(0, len(samples) - win + 1, win):
        chunk = samples[at : at + win]
        i = sum(map(operator.mul, chunk, cos))
        q = sum(map(operator.mul, chunk, sin))
        power = sum(map(operator.mul, chunk, chunk))
        out.append((2 * math.hypot(i, q) / win / 32768, math.sqrt(power / win) / 32768))
    return out


def _runs(samples: array[int], rate: int, pitch: float) -> list[tuple[float, float]]:
    """(start, length) in seconds of every stretch that is mostly a tone at pitch.

    The click track is two pure tones, so a mark is looked for by its pitch and
    not by loudness alone: a door, a voice or a key press is loud at every
    pitch at once and is passed over, however loud it is.
    """
    levels = _tone_levels(samples, rate, pitch)
    if not levels:
        return []
    threshold = max(FLOOR, OVER_ROOM * statistics.median(tone for tone, _ in levels))
    stretches, start, last = [], None, 0
    for n, (tone, loud) in enumerate(levels):
        if tone >= threshold and tone / math.sqrt(2) >= PURE * loud:
            if start is None:
                start = n
            last = n
        elif start is not None and n - last > GAP_WINDOWS:
            stretches.append((start, last))
            start = None
    if start is not None:
        stretches.append((start, last))
    found = []
    for first, final in stretches:
        # A room rings on after a mark, far more quietly. The mark itself is
        # the part within reach of its own loudest moment.
        body = BODY * max(tone for tone, _ in levels[first : final + 1])
        strong = [n for n in range(first, final + 1) if levels[n][0] >= body]
        found.append((strong[0] * WINDOW_S, (strong[-1] - strong[0] + 1) * WINDOW_S))
    return found


def marks(samples: array[int], rate: int) -> list[tuple[str, float]]:
    """(kind, start in seconds) of every click and beep heard, in order."""
    found = []
    for start, length in _runs(samples, rate, CLICK_HZ):
        # A click is 8 ms; a voice that passes through its pitch lasts longer
        # and is left out.
        if length <= CLICK_HEARD_MAX_S:
            found.append(("click", start))
    for start, length in _runs(samples, rate, BEEP_HZ):
        if length > (clicks.TEN_S + clicks.MINUTE_S) / 2:
            found.append(("minute", start))
        elif length > (clicks.CLICK_S + clicks.TEN_S) / 2:
            found.append(("ten", start))
    found.sort(key=lambda mark: mark[1])
    # Marks are a second apart. Anything this soon after one is its own ringing,
    # cut loose from it by a lost packet, and not a mark of its own.
    kept: list[tuple[str, float]] = []
    for kind, start in found:
        if not kept or start - kept[-1][1] >= ECHO_S:
            kept.append((kind, start))
    return kept


def offsets(found: list[tuple[str, float]], started_at: float) -> list[tuple[str, float, float]]:
    """(kind, heard_at, offset) per mark. offset > 0: heard after its true time."""
    period = {"click": 1, "ten": 10, "minute": 60}
    out = []
    for kind, start in found:
        heard = started_at + start
        true = round(heard / period[kind]) * period[kind]
        out.append((kind, heard, heard - true))
    return out


def summarize(rows: list[tuple[str, float, float]]) -> str:
    tens = [o for kind, _, o in rows if kind in ("ten", "minute")]
    ticks = [o for kind, _, o in rows if kind == "click"]
    if not tens:
        return "no ten-second beep heard: record at least 12 s"
    whole = statistics.median(tens)
    # A click only knows its fraction of a second; put it beside the beeps.
    near = [o + round(whole - o) for o in ticks] + tens
    return (
        f"offset {statistics.median(near):+.3f} s "
        f"(min {min(near):+.3f}, max {max(near):+.3f}, n={len(near)}; "
        f"from {len(tens)} ten-second beeps and {len(ticks)} clicks)"
    )


def read_wav(path: Path) -> tuple[array[int], int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:  # noqa: PLR2004
            raise SystemExit("need 16-bit PCM; convert with: ffmpeg -i in -c:a pcm_s16le out.wav")
        frames = array("h", w.readframes(w.getnframes()))
        channels = w.getnchannels()
        return (array("h", frames[::channels]) if channels > 1 else frames), w.getframerate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offset of a recorded click track from the clock")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--started-at", type=float, required=True)
    args = parser.parse_args()
    samples, rate = read_wav(args.wav)
    rows = offsets(marks(samples, rate), args.started_at)
    for kind, heard, offset in rows:
        print(f"{kind:6s} heard {heard:.3f}  offset {offset:+.3f}")
    print(summarize(rows))


if __name__ == "__main__":
    main()
