"""The click track: a sound whose every sample is a known wall-clock instant.

A short click on every wall-clock second, a longer and higher beep on every
tenth second, and a longer one still on every minute. Hearing a click tells you
the fraction of a second a speaker is off by; the ten-second and minute beeps
tell you the whole seconds.

Shared by the server (which plays it) and the analyser (which listens for it),
so the two cannot disagree about what a click is.
"""

from __future__ import annotations

import array
import math

SAMPLE_RATE = 48000

CLICK_S = 0.008  # every second: 8 ms at 1 kHz
TEN_S = 0.120  # every tenth second: 120 ms at 2 kHz
MINUTE_S = 0.360  # every minute: 360 ms at 2 kHz
AMPLITUDE = 0.6


def mark_for(second: int) -> tuple[float, float]:
    """The length and pitch of the sound that starts at a whole second."""
    if second % 60 == 0:
        return MINUTE_S, 2000.0
    if second % 10 == 0:
        return TEN_S, 2000.0
    return CLICK_S, 1000.0


def pcm(start_time: float, first_sample: int, count: int) -> bytes:
    """count mono 16-bit samples, the first being sample first_sample of a
    stream whose sample 0 sounds at wall-clock start_time (epoch seconds)."""
    out = array.array("h", bytes(2 * count))
    t0 = start_time + first_sample / SAMPLE_RATE
    t1 = t0 + count / SAMPLE_RATE
    for second in range(math.floor(t0) - 1, math.floor(t1) + 1):
        length, pitch = mark_for(second)
        lo = max(0, math.ceil((second - t0) * SAMPLE_RATE))
        hi = min(count, math.ceil((second + length - t0) * SAMPLE_RATE))
        for i in range(lo, hi):
            into = t0 + i / SAMPLE_RATE - second
            out[i] = int(32767 * AMPLITUDE * math.sin(2 * math.pi * pitch * into))
    return out.tobytes()
