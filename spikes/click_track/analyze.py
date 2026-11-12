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
import statistics
import sys
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import clicks  # noqa: E402

WINDOW_S = 0.001  # envelope resolution: 1 ms
QUIET_S = 0.200  # a mark is over after this much quiet


def marks(samples: array[int], rate: int, threshold: float) -> list[tuple[float, float]]:
    """(start, length) in seconds of every burst louder than threshold."""
    win = max(1, int(rate * WINDOW_S))
    loud = [
        max(abs(s) for s in samples[i : i + win]) / 32768 >= threshold
        for i in range(0, len(samples) - win, win)
    ]
    found, start, last = [], None, 0
    for i, on in enumerate(loud):
        if on:
            if start is None:
                start = i
            last = i
        elif start is not None and (i - last) * WINDOW_S > QUIET_S:
            found.append((start * WINDOW_S, (last - start + 1) * WINDOW_S))
            start = None
    if start is not None:
        found.append((start * WINDOW_S, (last - start + 1) * WINDOW_S))
    return found


def offsets(found: list[tuple[float, float]], started_at: float) -> list[tuple[str, float, float]]:
    """(kind, heard_at, offset) per mark. offset > 0: heard after its true time."""
    out = []
    for start, length in found:
        heard = started_at + start
        if length > (clicks.TEN_S + clicks.MINUTE_S) / 2:
            kind, period = "minute", 60
        elif length > (clicks.CLICK_S + clicks.TEN_S) / 2:
            kind, period = "ten", 10
        else:
            kind, period = "click", 1
        true = round(heard / period) * period
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
    parser.add_argument("--threshold", type=float, default=0.1, help="0..1, of full scale")
    args = parser.parse_args()
    samples, rate = read_wav(args.wav)
    rows = offsets(marks(samples, rate, args.threshold), args.started_at)
    for kind, heard, offset in rows:
        print(f"{kind:6s} heard {heard:.3f}  offset {offset:+.3f}")
    print(summarize(rows))
    if not math.isfinite(args.started_at):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
