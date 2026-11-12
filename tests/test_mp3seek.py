"""mp3seek.py against Static's reference, on generated tone files.

seek-expected.json holds what t/mp3seek_test.pl in static-squeezebox read from
each file and the byte it would seek to. tools/make_mp3_fixtures.sh makes the
files with ffmpeg (tones and noise, never episode audio) and saves the answers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mp3seek import (
    FRAME_BYTES,
    HEAD_BYTES,
    MAX_TAG_BYTES,
    Mp3Info,
    RangeRequest,
    byte_for,
    first_frame,
    id3_size,
    plan_probe,
)

MP3 = Path(__file__).parent / "fixtures" / "mp3"
EXPECTED: list[dict[str, Any]] = json.loads((MP3 / "seek-expected.json").read_text("utf-8"))
FILES = sorted({row["file"] for row in EXPECTED})


def _as_reference_reads(name: str) -> Mp3Info:
    """Read a file the way t/mp3seek_test.pl does: FRAME_BYTES from the tag's end."""
    data = (MP3 / name).read_bytes()
    tag = id3_size(data)
    info = first_frame(data[tag : tag + FRAME_BYTES], tag, len(data))
    assert info is not None
    return info


def test_the_fixtures_cover_every_way_of_seeking() -> None:
    kinds = {name: _as_reference_reads(name) for name in FILES}
    assert kinds["cbr-bare.mp3"].byte_rate and not kinds["cbr-bare.mp3"].frames
    assert kinds["cbr-info.mp3"].frames and not kinds["cbr-info.mp3"].toc
    assert kinds["vbr-xing.mp3"].toc and kinds["vbr-xing.mp3"].vbr
    assert kinds["mpeg2-bare.mp3"].samples_per_frame == 576
    assert kinds["big-tag.mp3"].audio_start > HEAD_BYTES - FRAME_BYTES


@pytest.mark.parametrize("row", EXPECTED, ids=lambda r: f"{r['file']} at {r['seconds']} s")
def test_header_and_byte_match_the_reference(row: dict[str, Any]) -> None:
    data = (MP3 / row["file"]).read_bytes()
    assert len(data) == row["total"]
    assert id3_size(data) == row["tag"]

    info = _as_reference_reads(row["file"])
    assert info.audio_start == row["start"]
    assert info.kbps == row["kbps"]
    assert info.sample_rate == row["srate"]
    assert info.walked == row["walked"]
    assert info.vbr == row["vbr"]
    assert (info.toc is not None) == row["toc"]
    assert info.frames == row["frames"]
    if row["byte_rate"] is None:
        assert info.byte_rate is None
    else:
        assert f"{info.byte_rate:.1f}" == row["byte_rate"]

    assert byte_for(info, row["seconds"], row["duration"]) == row["byte"]


@pytest.mark.parametrize("name", [f for f in FILES if f != "big-tag.mp3"])
def test_one_read_is_enough_without_a_big_tag(name: str) -> None:
    data = (MP3 / name).read_bytes()
    info = plan_probe(data[:HEAD_BYTES], len(data))
    assert isinstance(info, Mp3Info)
    assert info.audio_start == id3_size(data)
    assert info.total_bytes == len(data)


def test_a_big_tag_needs_a_second_read_from_its_end() -> None:
    data = (MP3 / "big-tag.mp3").read_bytes()
    tag = id3_size(data)
    assert tag > HEAD_BYTES  # the whole first read is tag

    ask = plan_probe(data[:HEAD_BYTES], len(data))
    assert ask == RangeRequest(start=tag, length=FRAME_BYTES)

    info = first_frame(data[ask.start : ask.start + ask.length], ask.start, len(data))
    assert info == _as_reference_reads("big-tag.mp3")
    # Seeking by bitrate from the top of the file would land inside the tag.
    assert byte_for(info, 1.0) > tag


def test_a_tag_too_big_to_be_real_is_given_up_on() -> None:
    size = MAX_TAG_BYTES + 1
    syncsafe = bytes((size >> shift) & 0x7F for shift in (21, 14, 7, 0))
    head = b"ID3\x03\x00\x00" + syncsafe + bytes(100)
    assert plan_probe(head, 50_000_000) is None


def test_bytes_that_are_not_an_mp3_give_nothing() -> None:
    assert plan_probe(bytes(HEAD_BYTES), HEAD_BYTES) is None
    assert first_frame(b"\x00\x00\x00\x18ftypM4A " + bytes(FRAME_BYTES), 0, None) is None
    assert id3_size(b"ID3\x03\x00\x00\xff\xff\xff\xff") == 0  # not syncsafe


def test_an_id3_footer_counts_toward_the_tag() -> None:
    assert id3_size(b"ID3\x04\x00\x10\x00\x00\x02\x01") == 10 + 257 + 10


def test_vbr_without_a_table_uses_the_episodes_published_length() -> None:
    info = Mp3Info(
        audio_start=1000,
        kbps=128,
        sample_rate=44100,
        samples_per_frame=1152,
        total_bytes=1000 + 3_600_000,
        walked=40,
        vbr=True,
        byte_rate=None,
        frames=None,
        audio_bytes=None,
        toc=None,
    )
    # A quarter of the way through an hour: a quarter of the audio bytes.
    assert byte_for(info, 900, 3600) == 1000 + 900_000
    # With no length to go by, the first frame's bitrate is all there is.
    assert byte_for(info, 900) == 1000 + 900 * 16_000


def test_past_the_end_of_a_table_is_the_end_of_the_audio() -> None:
    info = _as_reference_reads("vbr-xing.mp3")
    assert info.audio_bytes is not None
    assert byte_for(info, 10_000) == info.audio_start + info.audio_bytes
