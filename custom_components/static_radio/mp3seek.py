"""Where in an MP3 file is second N?

Tuning in means starting an episode partway through, at a byte offset. The
answer has to come from the file itself: the real size of its ID3 tag, then
the Xing table of contents for a variable-bitrate file, or the measured byte
rate for a constant-bitrate one. Dividing by an average bitrate instead put
one variable-bitrate episode 53 s late in Static's own testing, and a large
tag (cover art) shifts everything after it.

A port of MP3Seek.pm from Static's reference player, static-squeezebox
(https://github.com/madewith-bestpractice/static-squeezebox), checked against
its t/mp3seek_test.pl. This file does no I/O and imports nothing from Home
Assistant: plan_probe() says which bytes to fetch, and the caller fetches them.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEAD_BYTES = 32768  # the first ranged read: the tag, and usually the first frames too
FRAME_BYTES = 16384  # enough frames to measure the byte rate (about 39 at 128 kbps)
MAX_TAG_BYTES = 4 * 1024 * 1024  # cover art can make a tag big; past this, give up

_SCAN_BYTES = 8192  # how far to look for the first frame's sync
_MIN_WALKED_FRAMES = 30  # fewer than this is too few to trust a measured byte rate

# Bitrates in kbps by header index, layer III only.
_BITRATES = (
    (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),  # MPEG1
    (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),  # MPEG2 and 2.5
)
# Sample rates by version (MPEG1, MPEG2, MPEG2.5) and header index.
_SAMPLE_RATES = ((44100, 48000, 32000), (22050, 24000, 16000), (11025, 12000, 8000))

# Side-info length by [is MPEG1][is mono].
_SIDE_INFO_BYTES = ((17, 9), (32, 17))

_VERSION_MPEG1 = 3
_VERSION_MPEG2 = 2
_VERSION_RESERVED = 1
_LAYER_III = 1
_MONO = 3


@dataclass(frozen=True, slots=True)
class Mp3Info:
    """What the start of an MP3 file says about where its audio is."""

    audio_start: int  # file offset of the first frame, after any ID3 tag
    kbps: int  # the first frame's bitrate
    sample_rate: int
    samples_per_frame: int
    total_bytes: int | None  # the whole file's size, when the host reported it
    walked: int  # how many frames were measured
    vbr: bool  # the measured frames mix bitrates
    byte_rate: float | None  # measured bytes per second, padding included; CBR only
    frames: int | None  # from a Xing or Info header
    audio_bytes: int | None  # from a Xing or Info header
    toc: tuple[int, ...] | None  # the 100-point Xing table of contents


@dataclass(frozen=True, slots=True)
class RangeRequest:
    """Bytes the caller still has to fetch before the file can be read."""

    start: int
    length: int


def id3_size(head: bytes) -> int:
    """The size of the ID3v2 tag at the start of the file, or 0 without one."""
    if len(head) < 10 or head[:3] != b"ID3":  # noqa: PLR2004
        return 0
    size = head[6:10]
    if (size[0] | size[1] | size[2] | size[3]) & 0x80:
        return 0  # not syncsafe: not a real tag
    footer = 10 if head[5] & 0x10 else 0
    return 10 + (size[0] << 21 | size[1] << 14 | size[2] << 7 | size[3]) + footer


def first_frame(data: bytes, at: int, total_bytes: int | None) -> Mp3Info | None:  # noqa: PLR0915
    """Read the first frame, and its Xing or Info header, from data.

    data starts at file offset at (the end of the ID3 tag). Returns None when
    there is no layer III frame to read.
    """
    length = len(data)
    i = 0
    while i + 4 < length and i < _SCAN_BYTES:
        b0, b1, b2, b3 = data[i], data[i + 1], data[i + 2], data[i + 3]
        i += 1
        if b0 != 0xFF or (b1 & 0xE0) != 0xE0:  # noqa: PLR2004
            continue
        version = (b1 >> 3) & 3
        layer = (b1 >> 1) & 3
        bitrate_index, rate_index, channels = b2 >> 4, (b2 >> 2) & 3, b3 >> 6
        if (
            version == _VERSION_RESERVED
            or layer != _LAYER_III
            or bitrate_index in (0, 15)
            or rate_index == 3  # noqa: PLR2004
        ):
            continue
        start = i - 1
        mpeg1 = version == _VERSION_MPEG1
        bitrates = _BITRATES[0 if mpeg1 else 1]
        rates = _SAMPLE_RATES[0 if mpeg1 else (1 if version == _VERSION_MPEG2 else 2)]
        sample_rate = rates[rate_index]
        samples_per_frame = 1152 if mpeg1 else 576

        # The real byte rate: walk the frames we have. Some encoders under-pad,
        # so a "56 kbps" file can really be 55.7 (15 s off an hour in). A mix
        # of bitrates means VBR, where only a table or the average will do.
        p, walked, walked_bytes = start, 0, 0
        seen: set[int] = set()
        while p + 4 <= length:
            c0, c1, c2 = data[p], data[p + 1], data[p + 2]
            if c0 != 0xFF or (c1 & 0xE0) != 0xE0 or ((c1 >> 3) & 3) != version:  # noqa: PLR2004
                break
            frame_bitrate = c2 >> 4
            if frame_bitrate in (0, 15) or ((c2 >> 2) & 3) != rate_index:
                break
            frame_len = int(
                (144000 if mpeg1 else 72000) * bitrates[frame_bitrate] / sample_rate
            ) + ((c2 >> 1) & 1)
            if p + frame_len > length:
                break
            seen.add(frame_bitrate)
            walked += 1
            walked_bytes += frame_len
            p += frame_len
        vbr = len(seen) > 2  # noqa: PLR2004  (the Xing or Info frame itself may differ)
        byte_rate = None
        if walked >= _MIN_WALKED_FRAMES and not vbr:
            byte_rate = walked_bytes / (walked * samples_per_frame / sample_rate)

        # Xing or Info follows the side info: MPEG1 stereo 32, mono 17;
        # MPEG2 stereo 17, mono 9.
        x = start + 4 + _SIDE_INFO_BYTES[mpeg1][channels == _MONO]
        tag = data[x : x + 4] if x + 8 <= length else b""
        frames = audio_bytes = None
        toc = None
        if tag in (b"Xing", b"Info"):
            (flags,) = struct.unpack(">I", data[x + 4 : x + 8])
            q = x + 8
            if flags & 1 and q + 4 <= length:
                (frames,) = struct.unpack(">I", data[q : q + 4])
                q += 4
            if flags & 2 and q + 4 <= length:
                (audio_bytes,) = struct.unpack(">I", data[q : q + 4])
                q += 4
            if tag == b"Xing" and flags & 4 and q + 100 <= length:
                toc = tuple(data[q : q + 100])

        return Mp3Info(
            audio_start=at + start,
            kbps=bitrates[bitrate_index],
            sample_rate=sample_rate,
            samples_per_frame=samples_per_frame,
            total_bytes=total_bytes,
            walked=walked,
            vbr=vbr,
            byte_rate=byte_rate,
            frames=frames,
            audio_bytes=audio_bytes,
            toc=toc,
        )
    return None


def plan_probe(head: bytes, total_bytes: int | None) -> Mp3Info | RangeRequest | None:
    """Read the file's first HEAD_BYTES, or say what else is needed.

    Returns the info when the head already holds the first frames, a
    RangeRequest for the frames when a large tag pushed them out of the head
    (answer it with first_frame(data, request.start, total_bytes)), or None
    when this is not an MP3 that can be read.
    """
    tag = id3_size(head)
    if tag + FRAME_BYTES <= len(head):
        return first_frame(head[tag:], tag, total_bytes)
    if tag > MAX_TAG_BYTES:
        return None
    return RangeRequest(start=tag, length=FRAME_BYTES)


def byte_for(info: Mp3Info, seconds: float, duration_s: float | None = None) -> int:
    """The byte offset of a time in the file.

    duration_s is the episode's published length, used only for a
    variable-bitrate file that carries no table.
    """
    if seconds <= 0:
        return info.audio_start
    total = info.total_bytes or 0
    audio = info.audio_bytes or (total - info.audio_start if total > info.audio_start else 0)
    if info.frames and audio:
        file_s = info.frames * info.samples_per_frame / info.sample_rate
        if info.toc:
            # VBR: interpolate the 100-point Xing table.
            pct = seconds * 100 / file_s
            if pct >= 100:  # noqa: PLR2004
                return info.audio_start + audio
            a = int(pct)
            fa = info.toc[a]
            fb = info.toc[a + 1] if a < 99 else 256  # noqa: PLR2004
            return info.audio_start + int((fa + (fb - fa) * (pct - a)) / 256 * audio)
        # A Xing or Info header without a table: its frame count gives the
        # true average.
        return info.audio_start + int(seconds / file_s * audio)
    if info.vbr and duration_s and audio:
        # VBR without a table: the file's average over the episode's length.
        return info.audio_start + int(seconds / duration_s * audio)
    # CBR: the measured byte rate (padding included), else the frame bitrate.
    return info.audio_start + int(seconds * (info.byte_rate or info.kbps * 1000 / 8))
