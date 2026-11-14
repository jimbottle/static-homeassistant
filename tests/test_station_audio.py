"""station_audio.py with real ffmpeg and generated tones: never an episode.

Each test builds a two-episode station out of tones made on the spot, tunes
in, and checks the samples that come out: how many, and what is in them.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import itertools
import os
import shutil
import subprocess
import threading
import time
from array import array
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from custom_components.static_radio import station_audio
from custom_components.static_radio.schedule import GAP_MS, Episode, Schedule, Station
from custom_components.static_radio.segments import OPENING_MS
from custom_components.static_radio.station_audio import (
    BYTES_PER_MS,
    MAX_RETRIES,
    SAMPLE_RATE,
    EpisodeFailedError,
    Listen,
    Source,
    station_pcm,
)

FFMPEG = shutil.which("ffmpeg")
if FFMPEG is None and os.environ.get("CI"):
    raise RuntimeError("these tests need ffmpeg, and CI is meant to have it")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

NOW_MS = 1_800_000_000_000
LEAD_MS = 1300


def tone(hz: int, ms: int) -> bytes:
    """An MP3 of a sine tone, made in memory."""
    command = [
            FFMPEG or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={hz}:sample_rate=44100:duration={ms / 1000}",
            "-b:a",
            "64k",
            "-f",
            "mp3",
            "pipe:1",
    ]  # fmt: skip
    return subprocess.run(command, capture_output=True, check=True).stdout  # noqa: S603


def episode(name: str, duration_ms: int, *, bulletin: bool = False) -> Episode:
    return Episode(
        episode_id=name,
        audio_url=f"https://shows.example/{name}.mp3",
        duration_ms=duration_ms,
        title=name,
        feed_title="A Show",
        episode_url="",
        feed_url="",
        image_url="",
        gain_db=0.0,
        is_bulletin=bulletin,
        support_url=None,
        placement_id=None,
    )


def schedule_of(*episodes: Episode, first_starts_ms: int) -> Schedule:
    station = Station(
        station_id="test_01",
        display_name="Test",
        u_tag="TEST",
        sort_index=0,
        loop_ms=sum(e.duration_ms for e in episodes),
        episodes=episodes,
    )
    return Schedule(
        schema_version=8, epoch_ms=first_starts_ms, generated_at_utc="", stations=(station,)
    )


def heard_first_ms(into_ms: int) -> int:
    """The epoch that puts the stream's first episode sample into_ms into episode one."""
    return NOW_MS + LEAD_MS + OPENING_MS - into_ms


class Host:
    """A show's host, serving tones from memory and counting what it is asked for."""

    def __init__(self, files: dict[str, bytes], *, fail: bool = False, slow_s: float = 0) -> None:
        self.files = files
        self.fail = fail
        self.slow_s = slow_s
        self.asked: list[tuple[str, int]] = []
        self.paths: dict[str, str] = {}  # episodes ffmpeg is to fetch itself

    async def open(self, ep: Episode, start_ms: int) -> Source:
        self.asked.append((ep.episode_id, start_ms))
        if self.slow_s:
            await asyncio.sleep(self.slow_s)
        if self.fail:
            raise EpisodeFailedError("503 from the host")
        if ep.episode_id in self.paths:
            return Source(url=self.paths[ep.episode_id], seek_ms=start_ms)
        return Source(chunks=self._chunks(ep, start_ms))

    async def _chunks(self, ep: Episode, start_ms: int) -> AsyncIterator[bytes]:
        data = self.files[ep.episode_id]
        # A constant-bitrate tone: a time is the same share of the bytes.
        made_ms = len(data) * 8 // 64
        at = len(data) * min(start_ms, made_ms) // max(made_ms, 1)
        for i in range(at, len(data), 4096):
            yield data[i : i + 4096]


def collect(listen: Listen, ms: int) -> bytes:
    """The first ms of a listen's stream."""

    async def run() -> bytes:
        out = bytearray()
        stream = station_pcm(listen)
        try:
            async for chunk in stream:
                out += chunk
                if len(out) >= ms * BYTES_PER_MS:
                    break
        finally:
            await stream.aclose()
        return bytes(out[: ms * BYTES_PER_MS])

    return asyncio.run(run())


def listen_to(schedule: Schedule | None, host: Host, *, clock=lambda: NOW_MS) -> Listen:  # type: ignore[no-untyped-def]
    return Listen(
        station_id="test_01",
        lead_ms=LEAD_MS,
        now_ms=clock,
        schedule=lambda: schedule,
        open_episode=host.open,
        ffmpeg=FFMPEG or "ffmpeg",
    )


def part(pcm: bytes, from_ms: int, to_ms: int) -> array[int]:
    """The left channel of a stretch of the stream."""
    both = array("h", pcm[from_ms * BYTES_PER_MS : to_ms * BYTES_PER_MS])
    return both[::2]


def loudness(samples: array[int]) -> float:
    return max((abs(s) for s in samples), default=0) / 32768


def pitch(samples: array[int]) -> float:
    """The tone's frequency, from how often it crosses zero."""
    crossings = sum(1 for a, b in itertools.pairwise(samples) if (a < 0) != (b < 0))
    return crossings / 2 / (len(samples) / SAMPLE_RATE)


def test_the_stream_follows_the_timeline_sample_for_sample() -> None:
    host = Host({"one": tone(440, 4000), "two": tone(880, 3000)})
    schedule = schedule_of(
        episode("one", 4000), episode("two", 3000), first_starts_ms=heard_first_ms(1500)
    )
    # The opening, the 2.5 s left of episode one, its gap, episode two, its gap.
    total = OPENING_MS + 2500 + GAP_MS + 3000 + GAP_MS
    pcm = collect(listen_to(schedule, host), total)

    assert len(pcm) == total * BYTES_PER_MS
    assert host.asked[:2] == [("one", 1500), ("two", 0)]
    at = OPENING_MS
    assert loudness(part(pcm, 0, at)) == 0
    assert pitch(part(pcm, at + 300, at + 2300)) == pytest.approx(440, abs=5)
    assert loudness(part(pcm, at + 2500 + 100, at + 2500 + GAP_MS - 100)) < 0.01
    at += 2500 + GAP_MS
    assert pitch(part(pcm, at + 300, at + 2700)) == pytest.approx(880, abs=5)
    assert loudness(part(pcm, at + 3000 + 100, at + 3000 + GAP_MS - 100)) < 0.01


def test_a_file_that_runs_long_is_cut_at_its_scheduled_end() -> None:
    host = Host({"one": tone(440, 6000), "two": tone(880, 3000)})
    schedule = schedule_of(
        episode("one", 2000), episode("two", 3000), first_starts_ms=heard_first_ms(0)
    )
    pcm = collect(listen_to(schedule, host), OPENING_MS + 2000 + GAP_MS + 2000)
    at = OPENING_MS
    assert pitch(part(pcm, at + 300, at + 1900)) == pytest.approx(440, abs=5)
    assert loudness(part(pcm, at + 2000 + 20, at + 2000 + GAP_MS - 20)) == 0
    at += 2000 + GAP_MS
    assert pitch(part(pcm, at + 300, at + 1900)) == pytest.approx(880, abs=5)


def test_a_file_that_ends_early_is_followed_by_silence_not_the_next_episode() -> None:
    host = Host({"one": tone(440, 1500), "two": tone(880, 3000)})
    schedule = schedule_of(
        episode("one", 4000), episode("two", 3000), first_starts_ms=heard_first_ms(0)
    )
    pcm = collect(listen_to(schedule, host), OPENING_MS + 4000 + GAP_MS + 2000)
    at = OPENING_MS
    assert pitch(part(pcm, at + 200, at + 1300)) == pytest.approx(440, abs=8)
    assert loudness(part(pcm, at + 1800, at + 4000 + GAP_MS - 20)) < 0.01
    at += 4000 + GAP_MS
    assert pitch(part(pcm, at + 300, at + 1900)) == pytest.approx(880, abs=5)
    assert host.asked == [("one", 0), ("two", 0)]  # a short file is not a failure


def test_a_failing_host_is_tried_three_times_then_left_until_the_next_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(station_audio, "RETRY_MS", 200)
    host = Host({}, fail=True)
    schedule = schedule_of(
        episode("one", 4000), episode("two", 3000), first_starts_ms=heard_first_ms(0)
    )
    pcm = collect(listen_to(schedule, host), OPENING_MS + 4000 + GAP_MS)
    assert len(pcm) == (OPENING_MS + 4000 + GAP_MS) * BYTES_PER_MS
    assert loudness(part(pcm, 0, OPENING_MS + 4000 + GAP_MS)) == 0
    assert [name for name, _ in host.asked][: 1 + MAX_RETRIES] == ["one"] * (1 + MAX_RETRIES)
    assert [name for name, _ in host.asked].count("one") == 1 + MAX_RETRIES


def test_a_slow_host_costs_some_of_the_episode_and_none_of_the_timeline() -> None:
    """With a real clock: silence stands in, and the covered part is dropped."""
    host = Host({"one": tone(440, 8000), "two": tone(880, 3000)}, slow_s=1.8)
    started = time.time()
    schedule = schedule_of(
        episode("one", 8000),
        episode("two", 3000),
        first_starts_ms=int(started * 1000) + LEAD_MS + OPENING_MS,
    )
    total = OPENING_MS + 8000 + GAP_MS + 1500
    pcm = collect(listen_to(schedule, host, clock=lambda: int(time.time() * 1000)), total)
    assert len(pcm) == total * BYTES_PER_MS
    at = OPENING_MS
    # The reader had 3.2 s in hand and silence starts once that falls under
    # 2 s, so about 0.6 s of the episode is covered before the host answers.
    assert loudness(part(pcm, at, at + 300)) == 0
    assert pitch(part(pcm, at + 1500, at + 7500)) == pytest.approx(440, abs=5)
    # And episode two still starts on its own millisecond.
    at += 8000 + GAP_MS
    assert loudness(part(pcm, at - 300, at - 20)) < 0.01
    assert pitch(part(pcm, at + 300, at + 1400)) == pytest.approx(880, abs=8)


def test_with_no_schedule_the_stream_stays_quiet_and_keeps_going() -> None:
    host = Host({})
    pcm = collect(listen_to(None, host, clock=lambda: int(time.time() * 1000)), OPENING_MS + 1000)
    assert loudness(part(pcm, 0, OPENING_MS + 1000)) == 0
    assert host.asked == []


def test_the_spoken_opening_is_sent_first_and_padded_to_the_least_opening() -> None:
    spoken = (array("h", [1000, 1000]) * (SAMPLE_RATE // 2)).tobytes()  # half a second
    host = Host({"one": tone(440, 4000), "two": tone(880, 3000)})
    schedule = schedule_of(
        episode("one", 4000), episode("two", 3000), first_starts_ms=heard_first_ms(0)
    )
    listen = listen_to(schedule, host)
    listen = Listen(**{**{f: getattr(listen, f) for f in listen.__slots__}, "opening": spoken})
    pcm = collect(listen, OPENING_MS + 2000)
    assert pcm[: len(spoken)] == spoken
    assert loudness(part(pcm, 500, OPENING_MS)) == 0
    assert pitch(part(pcm, OPENING_MS + 300, OPENING_MS + 1900)) == pytest.approx(440, abs=5)


def test_a_file_ffmpeg_fetches_itself_starts_partway_even_with_its_index_at_the_end(
    tmp_path: Path,
) -> None:
    """An M4A as ffmpeg writes it by default: the index after the audio."""
    made = tmp_path / "one.m4a"
    command = [
        FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100:duration=6",
        "-c:a", "aac", str(made),
    ]  # fmt: skip
    subprocess.run(command, check=True)  # noqa: S603
    host = Host({"two": tone(880, 3000)})
    host.paths["one"] = str(made)
    schedule = schedule_of(
        episode("one", 6000), episode("two", 3000), first_starts_ms=heard_first_ms(2500)
    )
    pcm = collect(listen_to(schedule, host), OPENING_MS + 3500 + GAP_MS + 2000)
    assert host.asked[0] == ("one", 2500)
    at = OPENING_MS
    assert pitch(part(pcm, at + 200, at + 3300)) == pytest.approx(660, abs=8)
    assert loudness(part(pcm, at + 3500 + 50, at + 3500 + GAP_MS - 50)) < 0.01
    at += 3500 + GAP_MS
    assert pitch(part(pcm, at + 300, at + 1900)) == pytest.approx(880, abs=5)


def test_a_file_ffmpeg_cannot_fetch_is_a_failure_and_is_tried_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(station_audio, "RETRY_MS", 200)
    host = Host({"two": tone(880, 3000)})
    host.paths["one"] = str(tmp_path / "not-there.m4a")
    schedule = schedule_of(
        episode("one", 4000), episode("two", 3000), first_starts_ms=heard_first_ms(0)
    )
    pcm = collect(listen_to(schedule, host), OPENING_MS + 4000 + GAP_MS + 1500)
    assert loudness(part(pcm, 0, OPENING_MS + 4000 + GAP_MS)) == 0
    assert [name for name, _ in host.asked].count("one") == 1 + MAX_RETRIES
    at = OPENING_MS + 4000 + GAP_MS
    assert pitch(part(pcm, at + 300, at + 1400)) == pytest.approx(880, abs=8)


class _RangedFile(http.server.BaseHTTPRequestHandler):
    """A web server for one file that answers Range, as the relay does for ffmpeg."""

    data = b""

    def do_GET(self) -> None:
        asked = self.headers.get("Range")
        first, last = 0, len(self.data) - 1
        if asked:
            start, _, end = asked.removeprefix("bytes=").partition("-")
            first, last = int(start), int(end) if end else last
        self.send_response(206 if asked else 200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(last - first + 1))
        if asked:
            self.send_header("Content-Range", f"bytes {first}-{last}/{len(self.data)}")
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(self.data[first : last + 1])

    def log_message(self, *_args: object) -> None:
        pass


def test_ffmpeg_seeks_in_a_file_at_a_web_address_on_this_machine(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """The real shape of a non-MP3 listen: ffmpeg, a web address, Range requests."""
    # The Home Assistant harness blocks sockets unless a test asks; without the
    # harness there is nothing to ask.
    with contextlib.suppress(pytest.FixtureLookupError):
        request.getfixturevalue("socket_enabled")
    made = tmp_path / "one.m4a"
    command = [
        FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100:duration=6",
        "-c:a", "aac", str(made),
    ]  # fmt: skip
    subprocess.run(command, check=True)  # noqa: S603
    handler = type("OneFile", (_RangedFile,), {"data": made.read_bytes()})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host = Host({"two": tone(880, 3000)})
        host.paths["one"] = f"http://127.0.0.1:{server.server_address[1]}/one.m4a"
        schedule = schedule_of(
            episode("one", 6000), episode("two", 3000), first_starts_ms=heard_first_ms(2500)
        )
        pcm = collect(listen_to(schedule, host), OPENING_MS + 3500 + GAP_MS)
    finally:
        server.shutdown()
    assert host.asked == [("one", 2500)]  # heard at the first try: no failure, no retry
    at = OPENING_MS
    assert pitch(part(pcm, at + 200, at + 3300)) == pytest.approx(660, abs=8)
