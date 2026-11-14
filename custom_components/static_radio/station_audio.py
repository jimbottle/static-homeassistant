"""A station's sound: one endless run of samples, cut to Static's timeline.

segments.py says what belongs in the stream. This file makes it: for each
slot it asks for the episode's bytes from the right place, has ffmpeg decode
them, and passes on exactly as many samples as the slot holds. A file that
runs long is cut at its scheduled end, a short one is followed by silence,
and the stream as a whole neither gains nor loses a sample against the clock.

Three things here are Static's rules and not choices:

- The audio is never stored. Bytes go from the show's host through ffmpeg's
  pipes to the speaker, and what the speaker has not asked for is not fetched.
- Keep to the clock, not the file. The next episode starts on its scheduled
  millisecond whether or not this one's file has finished.
- An episode that fails is tried again twice, three seconds apart, at
  wherever live has got to, and is then left alone until the next episode.
  This is the reference player's rule (ProtocolHandler.pm, MAX_RETRIES and
  RETRY_S in static-squeezebox).

Sound goes out as fast as the speaker reads it. Only silence that stands in
for an episode still on its way is held to the clock, and whatever part of
the episode that silence covered is dropped when it arrives, so a slow host
costs a listener some of the episode and never their place in it.

Nothing from Home Assistant is imported: the clock, the schedule and the
episode's bytes are handed in, so this runs in a test with a generated tone.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .schedule import Episode, Schedule
from .segments import OPENING_MS, Segment, heard_at_ms, segment_at

_LOGGER = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
FRAME_BYTES = 2 * CHANNELS  # one 16-bit sample for each channel
BYTES_PER_MS = SAMPLE_RATE * FRAME_BYTES // 1000

MAX_RETRIES = 2  # tries after the first, per episode: the reference's MAX_RETRIES
RETRY_MS = 3000  # the wait before each: the reference's RETRY_S
MIN_AUDIO_MS = 1000  # with less than this left of an episode, wait for the next one quietly
STEP_MS = 100  # the size silence is sent in, and how often a slow episode is checked on
# Silence stands in for a late episode only once the speaker holds less than
# this much of the stream ahead of the clock. Until then, waiting costs nothing.
FILL_BELOW_MS = 2000
URL_TIMEOUT_S = 30  # how long ffmpeg waits on an address it reads from itself
NO_SCHEDULE_MS = 1000  # with no usable schedule, how much silence before looking again

_READ_BYTES = 32 * 1024


class EpisodeFailedError(Exception):
    """The show's host did not give the episode, or stopped partway."""


@dataclass(frozen=True, slots=True)
class Source:
    """Where ffmpeg gets an episode from: one of two ways.

    chunks: the file's bytes from the right place, passed to ffmpeg as they
    arrive. This is how an MP3 is played, from the byte mp3seek.py works out.

    url: an address ffmpeg reads from and seeks in to seek_ms itself. This is
    how every other kind of file is played: an MP4 keeps the index it cannot
    be played without anywhere in the file, often at the end, so it cannot be
    fed through a pipe. The address is never the show's host. It is this
    machine (episode.py's relay), which does the asking, so that ffmpeg is
    never the one following a stranger's redirects.

    release: called once ffmpeg is done with url, so the address stops working.
    """

    chunks: AsyncIterator[bytes] | None = None
    url: str | None = None
    seek_ms: int = 0
    release: Callable[[], None] | None = None


# An episode from start_ms in. Raises EpisodeFailedError when the host refuses,
# at once or partway through its bytes; the bytes end normally when the file does.
OpenEpisode = Callable[[Episode, int], Awaitable[Source]]


class Clock(Protocol):
    def __call__(self) -> int: ...  # epoch milliseconds


@dataclass(frozen=True, slots=True)
class Listen:
    """One speaker tuned to one station, and what the stream needs to serve it."""

    station_id: str
    lead_ms: int  # how long after connecting this speaker makes its first sound
    now_ms: Clock
    schedule: Callable[[], Schedule | None]  # the current one, each time it is asked
    open_episode: OpenEpisode
    ffmpeg: str  # the ffmpeg binary
    opening: bytes = b""  # the station's name, spoken, as samples; empty for silence


def silence(ms: int) -> bytes:
    return bytes(ms * BYTES_PER_MS)


class _Sent:
    """How much of the stream has gone out, against how long it has been going."""

    def __init__(self, now_ms: Clock) -> None:
        self._now_ms = now_ms
        self.connected_ms = now_ms()
        self.bytes = 0

    @property
    def ms(self) -> int:
        return self.bytes // BYTES_PER_MS

    def ahead_ms(self) -> int:
        """How far the stream sent so far runs past the clock."""
        return self.ms - (self._now_ms() - self.connected_ms)


def _decode_command(ffmpeg: str, gain_db: float, source: Source) -> list[str]:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if source.url is None:
        command += ["-i", "pipe:0"]
    else:
        if source.url.startswith(("http://", "https://")):
            # Web addresses only, and no waiting for ever. ffmpeg has no switch
            # to stop it following redirects, so the address it is given must
            # never answer with one: episode.py's relay turns each into an error.
            command += [
                "-protocol_whitelist", "http,https,tcp,tls",
                "-rw_timeout", str(URL_TIMEOUT_S * 1_000_000),
            ]  # fmt: skip
        command += ["-ss", f"{source.seek_ms / 1000:.3f}", "-i", source.url]
    return [
        *command, "-vn",
        "-af", f"volume={gain_db:.2f}dB",
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "pipe:1",
    ]  # fmt: skip


class _Decoder:
    """ffmpeg turning one episode into samples, with nothing on disk."""

    def __init__(self, listen: Listen, episode: Episode, start_ms: int) -> None:
        self._listen = listen
        self._episode = episode
        self._start_ms = start_ms
        self._process: asyncio.subprocess.Process | None = None
        self._feeder: asyncio.Task[None] | None = None
        self._fetches_itself = False
        self._release: Callable[[], None] | None = None
        self.failed = False  # the host let us down, as opposed to the file ending

    def _warn(self, err: object) -> None:
        _LOGGER.warning("%s: %s", self._episode.audio_url.split("?")[0], err)

    async def _open(self) -> None:
        """Ask for the episode and start ffmpeg on it. Slow when the host is."""
        source = await self._listen.open_episode(self._episode, self._start_ms)
        self._fetches_itself = source.url is not None
        self._release = source.release
        self._process = await asyncio.create_subprocess_exec(
            *_decode_command(self._listen.ffmpeg, self._episode.gain_db, source),
            stdin=asyncio.subprocess.DEVNULL if self._fetches_itself else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if source.chunks is not None:
            self._feeder = asyncio.create_task(self._feed(source.chunks))

    async def _feed(self, chunks: AsyncIterator[bytes]) -> None:
        """Pass the host's bytes to ffmpeg as fast as ffmpeg takes them."""
        assert self._process is not None and self._process.stdin is not None  # noqa: S101
        stdin = self._process.stdin
        try:
            async for data in chunks:
                stdin.write(data)
                await stdin.drain()
        except EpisodeFailedError as err:
            self.failed = True
            self._warn(err)
        except BrokenPipeError, ConnectionResetError:
            pass  # ffmpeg has gone: the slot ended, or it could not read the file
        finally:
            with contextlib.suppress(OSError):
                stdin.close()

    async def read(self) -> bytes:
        """The next samples; empty when there are no more."""
        if self._process is None:
            try:
                await self._open()
            except EpisodeFailedError as err:
                self.failed = True
                self._warn(err)
                return b""
        assert self._process is not None and self._process.stdout is not None  # noqa: S101
        data = await self._process.stdout.read(_READ_BYTES)
        if not data and self._fetches_itself and await self._process.wait():
            # ffmpeg did the fetching, so its way out is the only word on how it went.
            self.failed = True
            self._warn("ffmpeg could not fetch or read it")
        return data

    async def close(self) -> None:
        if self._feeder is not None:
            self._feeder.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._feeder
        if self._process is not None and self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
            await self._process.wait()
        if self._release is not None:
            self._release()
            self._release = None


@dataclass(slots=True)
class _Slot:
    """What is still owed of one episode's audio, in bytes of samples."""

    left: int
    failed: bool = False  # the last try ended because the host let us down

    def fill(self, sent: _Sent) -> bytes:
        """A step of silence if the speaker is about to run dry, else nothing."""
        if sent.ahead_ms() >= FILL_BELOW_MS:
            return b""
        step = min(self.left, STEP_MS * BYTES_PER_MS)
        self.left -= step
        return bytes(step)


async def _pause(sent: _Sent, slot: _Slot) -> AsyncIterator[bytes]:
    """The wait before a retry, on the clock, covered with silence only if needed."""
    for _ in range(RETRY_MS // STEP_MS):
        if slot.left <= 0:
            return
        await asyncio.sleep(STEP_MS / 1000)
        if step := slot.fill(sent):
            yield step


async def _one_try(
    listen: Listen, sent: _Sent, segment: Segment, slot: _Slot
) -> AsyncIterator[bytes]:
    """Decode the episode from where the slot has got to, until it ends or the slot does."""
    at_ms = segment.start_ms + segment.audio_ms - slot.left // BYTES_PER_MS
    decoder = _Decoder(listen, segment.episode, at_ms)
    skip = 0  # samples that silence already stood in for while this try was slow
    tail = b""  # bytes short of a whole sample frame, kept for the next read
    reading = asyncio.ensure_future(decoder.read())
    try:
        while slot.left > 0:
            done, _ = await asyncio.wait({reading}, timeout=STEP_MS / 1000)
            if not done:
                if step := slot.fill(sent):
                    skip += len(step)
                    yield step
                continue
            data = reading.result()
            if not data:
                break
            reading = asyncio.ensure_future(decoder.read())
            dropped = min(skip, len(data))
            skip -= dropped
            data = tail + data[dropped:]
            whole = len(data) - len(data) % FRAME_BYTES
            data, tail = data[:whole][: slot.left], data[whole:]
            if data:
                slot.left -= len(data)
                yield data
    finally:
        reading.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reading
        await decoder.close()
        slot.failed = decoder.failed


async def _episode_pcm(listen: Listen, sent: _Sent, segment: Segment) -> AsyncIterator[bytes]:
    """Exactly segment.audio_ms of sound: the episode, and silence where it fails."""
    slot = _Slot(left=segment.audio_ms * BYTES_PER_MS)
    for attempt in range(1 + MAX_RETRIES):
        if attempt:
            async for chunk in _pause(sent, slot):
                yield chunk
        if slot.left < MIN_AUDIO_MS * BYTES_PER_MS:
            break
        async for chunk in _one_try(listen, sent, segment, slot):
            yield chunk
        if not slot.failed:
            break  # the file ended, early or on time: stay quiet until the next one
    if slot.left > 0:
        yield bytes(slot.left)


async def station_pcm(listen: Listen) -> AsyncGenerator[bytes]:
    """The stream for one listen, from its first sample, for as long as it is read.

    Samples are 16-bit little-endian, SAMPLE_RATE, CHANNELS. The caller counts
    nothing: every chunk yielded here has been counted already.
    """
    sent = _Sent(listen.now_ms)

    async def counted(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        async for chunk in chunks:
            sent.bytes += len(chunk)
            yield chunk

    opening = listen.opening[: len(listen.opening) - len(listen.opening) % BYTES_PER_MS]
    opening += silence(max(0, OPENING_MS - len(opening) // BYTES_PER_MS))
    sent.bytes += len(opening)
    yield opening

    while True:
        at_ms = heard_at_ms(sent.connected_ms, listen.lead_ms, sent.ms)
        schedule = listen.schedule()
        station = schedule.station(listen.station_id) if schedule is not None else None
        segment = None
        # Another day's timeline is never played: stay quiet until today's arrives.
        if schedule is not None and station is not None and schedule.is_current_day(at_ms):
            segment = segment_at(schedule, station, at_ms)
        if segment is None:
            while sent.ahead_ms() >= FILL_BELOW_MS:
                await asyncio.sleep(STEP_MS / 1000)
            sent.bytes += NO_SCHEDULE_MS * BYTES_PER_MS
            yield silence(NO_SCHEDULE_MS)
            continue
        if segment.audio_ms:
            async for chunk in counted(_episode_pcm(listen, sent, segment)):
                yield chunk
        if segment.silence_ms:
            sent.bytes += segment.silence_ms * BYTES_PER_MS
            yield silence(segment.silence_ms)
