"""stream.py end to end: a speaker connects and gets its station, live, as FLAC.

The show's host is the harness's mock serving a generated tone, the clock is
pinned so the listen lands two seconds into an episode, and real ffmpeg does
the decoding and encoding. Nothing reaches a network and no episode is used.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import json
import os
import re
import select
import shutil
import subprocess
import sys
from array import array
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.static_radio import opening, stream
from custom_components.static_radio.api import SCHEDULE_URL
from custom_components.static_radio.const import CONF_LEADS_MS, CONF_SPEAKERS
from custom_components.static_radio.schedule import live, parse_schedule
from custom_components.static_radio.segments import OPENING_MS
from custom_components.static_radio.station_audio import BYTES_PER_MS, SAMPLE_RATE
from custom_components.static_radio.stream import CONF_STREAM_KEYS, _from_home, stream_path

FFMPEG = shutil.which("ffmpeg")
if FFMPEG is None and os.environ.get("CI"):
    raise RuntimeError("these tests need ffmpeg, and CI is meant to have it")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

SCHEDULE_WITH_LINKS_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures/schedule-wear.2026-11-12.json"
)
SPEAKER = "media_player.kitchen_speaker"
STATION = "news_01"
LEAD_MS = 1200
INTO_MS = 2000  # how far into its episode the listen lands
SCHEDULE = parse_schedule(json.loads(SCHEDULE_WITH_LINKS_FIXTURE.read_text("utf-8")))
AUDIO = re.compile(r"^https?://93\.184\.216\.34/.*")


def _pinned_now_ms() -> int:
    """A moment at which what is heard first is INTO_MS into an episode with plenty left."""
    station = SCHEDULE.station(STATION)
    assert station is not None
    at = SCHEDULE.epoch_ms + 3600_000
    while True:
        on_air = live(SCHEDULE, station, at)
        assert on_air is not None
        if not on_air.episode.is_bulletin and on_air.audio_ms > 60_000:
            return on_air.slot_start_ms + INTO_MS - LEAD_MS - OPENING_MS
        at = on_air.next_at_ms


NOW_MS = _pinned_now_ms()
Tuned = tuple[TestClient[Any, Any], MockConfigEntry, AiohttpClientMocker]


def _tone(ms: int, extra: tuple[str, ...] = ("-b:a", "64k", "-f", "mp3")) -> bytes:
    command = [
        FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={ms / 1000}",
        *extra, "pipe:1",
    ]  # fmt: skip
    return subprocess.run(  # noqa: S603
        command, capture_output=True, check=True, stdin=subprocess.DEVNULL, timeout=120
    ).stdout


@pytest.fixture(scope="module")
def tone() -> bytes:
    return _tone(30_000)


@pytest.fixture(autouse=True)
def _pinned() -> Iterator[None]:
    async def resolve(_hass: HomeAssistant, _host: str) -> list[str]:
        return ["93.184.216.34"]

    with (
        patch("custom_components.static_radio.feed._resolve", resolve),
        patch.object(stream, "now_ms", lambda: NOW_MS),
    ):
        yield


@pytest.fixture
async def tuned(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
    tone: bytes,
) -> Tuned:
    """Home Assistant with the integration set up, one speaker, and a show host that works."""
    aioclient_mock.get(SCHEDULE_URL, text=SCHEDULE_WITH_LINKS_FIXTURE.read_text("utf-8"))

    async def answer(method: str, url: object, _data: object) -> AiohttpClientMockResponse:
        asked = aioclient_mock.mock_calls[-1][3].get("Range")
        if asked is None:
            return AiohttpClientMockResponse(method, url, response=tone)
        first, _, last = asked.removeprefix("bytes=").partition("-")
        end = min(int(last), len(tone) - 1) if last else len(tone) - 1
        return AiohttpClientMockResponse(
            method, url, status=206, response=tone[int(first) : end + 1],
            headers={"Content-Range": f"bytes {first}-{end}/{len(tone)}"},
        )  # fmt: skip

    aioclient_mock.get(AUDIO, side_effect=answer)
    entry = MockConfigEntry(
        domain="static_radio",
        data={},
        options={CONF_SPEAKERS: [SPEAKER], CONF_LEADS_MS: {SPEAKER: LEAD_MS}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return await hass_client_no_auth(), entry, aioclient_mock


def _audio_calls(mock: AiohttpClientMocker) -> list[dict[str, str]]:
    return [call[3] for call in mock.mock_calls if AUDIO.match(str(call[1]))]


async def _read(
    client: TestClient[Any, Any], path: str, seconds: float
) -> tuple[dict[str, str], bytes]:
    """A listen's headers and whatever arrives in the first few seconds."""
    got = bytearray()
    response = await client.get(path)
    assert response.status == 200
    try:
        async with asyncio.timeout(seconds):
            async for chunk in response.content.iter_any():
                got += chunk
    except TimeoutError:
        pass
    finally:
        response.close()
    return dict(response.headers), bytes(got)


def _samples(flac: bytes) -> array[int]:
    """The left channel of a FLAC stream, cut off wherever it was."""
    command = [
        FFMPEG or "ffmpeg", "-hide_banner", "-loglevel", "quiet", "-nostdin", "-i", "pipe:0",
        "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
    ]  # fmt: skip
    pcm = subprocess.run(  # noqa: S603
        command, input=flac, capture_output=True, check=False, timeout=120
    ).stdout
    return array("h", pcm[: len(pcm) - len(pcm) % 4])[::2]


async def test_a_listen_is_the_station_live_as_an_endless_flac(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    client, entry, mock = tuned
    path = stream_path(hass, entry, SPEAKER, STATION, say=True)

    headers, flac = await _read(client, path, 4.0)

    assert headers["Content-Type"] == "audio/flac"
    assert headers["icy-name"] == "Static News"
    assert "Content-Length" not in headers
    assert flac.startswith(b"fLaC")
    left = _samples(flac)
    per_ms = SAMPLE_RATE // 1000
    assert len(left) > (OPENING_MS + 3000) * per_ms
    # Silence for the opening (no text-to-speech here), then the tone.
    assert max(abs(s) for s in left[: OPENING_MS * per_ms]) == 0
    heard = left[(OPENING_MS + 500) * per_ms : (OPENING_MS + 2500) * per_ms]
    crossings = sum(1 for a, b in itertools.pairwise(heard) if (a < 0) != (b < 0))
    assert crossings / 2 / 2 == pytest.approx(440, abs=5)
    # The host was asked for the header, then from two seconds in, as itself.
    calls = _audio_calls(mock)
    assert calls[0]["Range"] == "bytes=0-32767"
    start = int(calls[1]["Range"].removeprefix("bytes=").rstrip("-"))
    assert start == pytest.approx(INTO_MS * 64 // 8, rel=0.1)  # 64 kbps
    assert calls[1]["User-Agent"].startswith("HomeAssistant-Static/")


async def test_a_connection_that_drops_at_once_costs_the_shows_host_nothing(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    """A player looking the address over before playing it is not a listen."""
    client, entry, mock = tuned
    response = await client.get(stream_path(hass, entry, SPEAKER, STATION, say=True))
    assert (await response.content.readexactly(4)) == b"fLaC"
    response.close()
    await asyncio.sleep(stream.SETTLE_S + 0.4)
    assert _audio_calls(mock) == []


async def test_a_new_connection_for_a_speaker_ends_the_one_before(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    client, entry, _ = tuned
    path = stream_path(hass, entry, SPEAKER, STATION, say=True)
    first = await client.get(path)
    assert (await first.content.readexactly(4)) == b"fLaC"
    second = await client.get(path)
    assert (await second.content.readexactly(4)) == b"fLaC"
    async with asyncio.timeout(5):
        with pytest.raises(Exception):  # noqa: B017 - closed mid-stream, however aiohttp says it
            while await first.content.read(65536):
                pass
    second.close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/static_radio/stream/not-a-key/news_01.flac",
        "/api/static_radio/stream/{key}/no_such_station.flac",
        "/api/static_radio/stream/{key}/news_01.mp3",
    ],
)
async def test_a_wrong_key_station_or_format_is_not_found(
    hass: HomeAssistant,
    tuned: Tuned,
    path: str,
) -> None:
    client, entry, mock = tuned
    stream_path(hass, entry, SPEAKER, STATION, say=True)
    key = entry.data[CONF_STREAM_KEYS][SPEAKER]
    response = await client.get(path.format(key=key))
    assert response.status == 404
    assert _audio_calls(mock) == []


@pytest.mark.parametrize(
    ("remote", "home"),
    [
        ("127.0.0.1", True),
        ("192.168.1.20", True),
        ("10.0.0.7", True),
        ("::1", True),
        ("::ffff:192.168.1.20", True),
        ("172.30.32.2", True),  # the Supervisor's network, where an add-on calls from
        ("93.184.216.34", False),
        ("2606:2800:220:1:248:1893:25c8:1946", False),
        ("", False),
        (None, False),
    ],
)
def test_only_a_caller_on_the_home_network_is_served(remote: str | None, home: bool) -> None:
    assert _from_home(SimpleNamespace(remote=remote)) is home  # type: ignore[arg-type]


async def test_a_speakers_address_is_long_random_and_stays_the_same(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    _, entry, _ = tuned
    first = stream_path(hass, entry, SPEAKER, STATION, say=True)
    assert stream_path(hass, entry, SPEAKER, STATION, say=True) == first
    key = entry.data[CONF_STREAM_KEYS][SPEAKER]
    assert len(key) >= 32
    assert first == f"/api/static_radio/stream/{key}/{STATION}.flac"
    other = stream_path(hass, entry, "media_player.office_speaker", STATION, say=True)
    assert other != first
    # A tune asked for by voice leaves the spoken opening out.
    assert stream_path(hass, entry, SPEAKER, STATION, say=False) == f"{first}?quiet=1"


async def test_the_spoken_opening_is_ready_for_the_second_listen_not_the_first(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    station = SCHEDULE.station(STATION)
    assert station is not None
    said: list[str] = []
    wav = _tone(700, ("-f", "wav"))

    def source(_hass: HomeAssistant, message: str, **_how: object) -> str:
        said.append(message)
        return "media-source://tts/x"

    async def audio(_hass: HomeAssistant, _source: str) -> tuple[str, bytes]:
        return "wav", wav

    # Home Assistant's own text-to-speech cannot be imported here (its
    # requirements are not installed), so a stand-in takes its place.
    stand_in = SimpleNamespace(generate_media_source_id=source, async_get_media_source_audio=audio)
    # Setting up has already tried every name and found no text-to-speech.
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.config.components.add("tts")
    with patch.dict(sys.modules, {"homeassistant.components.tts": stand_in}):
        assert opening.opening_for(hass, FFMPEG or "ffmpeg", station) == b""
        await hass.async_block_till_done(wait_background_tasks=True)
        spoken = opening.opening_for(hass, FFMPEG or "ffmpeg", station)

    assert said == ["Playing Static News."]
    assert len(spoken) == pytest.approx(700 * BYTES_PER_MS, rel=0.05)


async def test_with_no_text_to_speech_the_opening_is_silence(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    station = SCHEDULE.station(STATION)
    assert station is not None
    assert opening.opening_for(hass, FFMPEG or "ffmpeg", station) == b""
    await hass.async_block_till_done(wait_background_tasks=True)
    assert opening.opening_for(hass, FFMPEG or "ffmpeg", station) == b""


OWN = [ipaddress.ip_network("2606:4700:1:2::/64"), ipaddress.ip_network("0.0.0.0/0")]


@pytest.mark.parametrize(
    ("remote", "home"),
    [
        ("2606:4700:1:2::77", True),  # a public address, on this machine's own network
        ("2606:4700:1:3::77", False),  # the network next door
        ("93.184.216.34", False),  # "everything" is a route out, not a home network
    ],
)
def test_a_neighbor_on_this_machines_own_network_is_at_home(remote: str, home: bool) -> None:
    assert _from_home(SimpleNamespace(remote=remote), OWN) is home  # type: ignore[arg-type]


async def test_an_open_stream_follows_the_entry_that_is_loaded_now(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    """Saving the Configure screen reloads the integration under a playing stream."""
    _, entry, _ = tuned
    view = hass.data[stream._VIEW_KEY]
    first = entry.runtime_data
    assert view._schedule() is first.data

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data is not first
    assert view._schedule() is entry.runtime_data.data

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert view._schedule() is None  # nothing to play, and nothing to fetch


async def test_streams_play_on_through_a_reload_and_end_after_a_removal(
    hass: HomeAssistant, tuned: Tuned
) -> None:
    client, entry, _ = tuned
    path = stream_path(hass, entry, SPEAKER, STATION, say=True)
    listening = await client.get(path)
    assert (await listening.content.readexactly(4)) == b"fLaC"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=stream.UNLOADED_FOR_S + 1))
    await hass.async_block_till_done()
    assert await listening.content.read(1024)  # still being served

    assert await hass.config_entries.async_unload(entry.entry_id)
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=2 * stream.UNLOADED_FOR_S + 2)
    )
    await hass.async_block_till_done()
    async with asyncio.timeout(10):
        with pytest.raises(Exception):  # noqa: B017 - closed mid-stream, however aiohttp says it
            while await listening.content.read(65536):
                pass


def test_the_encoder_sends_the_opening_on_without_waiting_for_more() -> None:
    """Some ffmpeg releases (8.0) hold their first output until they have seen
    five seconds of input, unless told there is nothing to look for."""
    command = stream._encode_command(FFMPEG or "ffmpeg")
    encoder = subprocess.Popen(  # noqa: S603
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    assert encoder.stdin is not None and encoder.stdout is not None
    try:
        encoder.stdin.write(bytes(OPENING_MS * BYTES_PER_MS))
        encoder.stdin.flush()
        ready, _, _ = select.select([encoder.stdout], [], [], 3)  # the input stays open
        assert ready, "nothing came out of the encoder within 3 s of the whole opening"
        assert os.read(encoder.stdout.fileno(), 4) == b"fLaC"
    finally:
        encoder.kill()
        encoder.wait()
