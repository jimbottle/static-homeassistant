"""episode.py asks a show's host for an episode the way Static's guide says to.

The host is the harness's mock, serving a tone generated on the spot, and
names resolve through a table the test controls. Nothing reaches a network.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.static_radio.episode import (
    MAX_REDIRECTS,
    EpisodeRelayView,
    _from_this_machine,
    episode_opener,
)
from custom_components.static_radio.schedule import Episode
from custom_components.static_radio.station_audio import EpisodeFailedError

FFMPEG = shutil.which("ffmpeg")
if FFMPEG is None and os.environ.get("CI"):
    raise RuntimeError("these tests need ffmpeg, and CI is meant to have it")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")

PUBLIC = "93.184.216.34"
NAMES = {
    "shows.example": [PUBLIC],
    "count.example": ["93.184.216.35"],
    "inside.example": ["10.0.0.5"],
}
URL = "https://shows.example/one.mp3"
AT = f"https://{PUBLIC}/one.mp3"
TONE_MS = 20_000


@pytest.fixture(scope="module")
def tone() -> bytes:
    command = [
            FFMPEG or "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={TONE_MS / 1000}",
            "-b:a",
            "64k",
            "-write_xing",
            "0",
            "-id3v2_version",
            "0",
            "-f",
            "mp3",
            "pipe:1",
    ]  # fmt: skip
    return subprocess.run(command, capture_output=True, check=True).stdout  # noqa: S603


@pytest.fixture(autouse=True)
def _names() -> Iterator[None]:
    async def resolve(_hass: HomeAssistant, host: str) -> list[str]:
        return NAMES.get(host, [host])

    with patch("custom_components.static_radio.feed._resolve", resolve):
        yield


def an_episode(url: str = URL) -> Episode:
    return Episode(
        episode_id="one", audio_url=url, duration_ms=TONE_MS, title="One", feed_title="A Show",
        episode_url="", feed_url="", image_url="", gain_db=0.0, is_bulletin=False,
        support_url=None, placement_id=None,
    )  # fmt: skip


def serve(mock: AiohttpClientMocker, at: str, data: bytes, *, ranges: bool = True) -> None:
    """A host that answers Range the way real ones do, or ignores it."""

    async def answer(method: str, url: object, _data: object) -> AiohttpClientMockResponse:
        asked = mock.mock_calls[-1][3].get("Range")
        if not ranges or asked is None:
            return AiohttpClientMockResponse(method, url, response=data)
        first, _, last = asked.removeprefix("bytes=").partition("-")
        end = min(int(last), len(data) - 1) if last else len(data) - 1
        return AiohttpClientMockResponse(
            method, url, status=206, response=data[int(first) : end + 1],
            headers={"Content-Range": f"bytes {first}-{end}/{len(data)}"},
        )  # fmt: skip

    mock.get(at, side_effect=answer)


async def read(hass: HomeAssistant, episode: Episode, start_ms: int) -> bytes:
    opener = episode_opener(hass, async_get_clientsession(hass), "0.0.0")
    source = await opener(episode, start_ms)
    assert source.chunks is not None
    return b"".join([chunk async for chunk in source.chunks])


async def test_from_the_top_is_one_plain_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tone: bytes
) -> None:
    serve(aioclient_mock, AT, tone)
    assert await read(hass, an_episode(), 0) == tone
    assert aioclient_mock.call_count == 1
    sent = aioclient_mock.mock_calls[0][3]
    assert "Range" not in sent
    assert sent["Host"] == "shows.example"
    assert sent["User-Agent"] == "HomeAssistant-Static/0.0.0"
    assert sent["Accept-Language"] == "en"


async def test_a_second_or_less_in_starts_at_the_top_without_asking(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tone: bytes
) -> None:
    serve(aioclient_mock, AT, tone)
    assert await read(hass, an_episode(), 1000) == tone
    assert aioclient_mock.call_count == 1


async def test_partway_in_reads_the_header_then_asks_from_the_right_byte(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tone: bytes
) -> None:
    serve(aioclient_mock, AT, tone)
    got = await read(hass, an_episode(), 10_000)
    ranges = [call[3].get("Range") for call in aioclient_mock.mock_calls]
    assert ranges[0] == "bytes=0-32767"
    start = int(ranges[-1].removeprefix("bytes=").rstrip("-"))
    # Halfway through a constant-bitrate file is halfway through its bytes.
    assert start == pytest.approx(len(tone) / 2, rel=0.02)
    assert got == tone[start:]
    assert len(ranges) == 2


async def test_a_host_without_ranges_is_played_from_the_top(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tone: bytes
) -> None:
    serve(aioclient_mock, AT, tone, ranges=False)
    assert await read(hass, an_episode(), 10_000) == tone
    assert "Range" not in aioclient_mock.mock_calls[-1][3]


async def test_redirects_are_followed_and_audio_url_itself_is_asked_each_time(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tone: bytes
) -> None:
    counted = "https://count.example/r/shows.example/one.mp3"
    aioclient_mock.get(
        "https://93.184.216.35/r/shows.example/one.mp3", status=302, headers={"Location": URL}
    )
    serve(aioclient_mock, AT, tone)
    assert await read(hass, an_episode(counted), 0) == tone
    assert await read(hass, an_episode(counted), 0) == tone
    hosts = [call[3]["Host"] for call in aioclient_mock.mock_calls]
    assert hosts == ["count.example", "shows.example"] * 2


@pytest.mark.parametrize(
    "location",
    ["https://inside.example/one.mp3", "http://127.0.0.1:8123/api/", "file:///etc/passwd"],
)
async def test_a_redirect_into_the_house_fails_the_episode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, location: str
) -> None:
    aioclient_mock.get(AT, status=302, headers={"Location": location})
    with pytest.raises(EpisodeFailedError):
        await read(hass, an_episode(), 0)
    assert aioclient_mock.call_count == 1


async def test_a_redirect_loop_gives_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(AT, status=302, headers={"Location": URL})
    with pytest.raises(EpisodeFailedError):
        await read(hass, an_episode(), 0)
    assert aioclient_mock.call_count == MAX_REDIRECTS + 1


@pytest.mark.parametrize("status", [403, 404, 500, 503])
async def test_a_refusal_fails_the_episode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    aioclient_mock.get(AT, status=status)
    with pytest.raises(EpisodeFailedError):
        await read(hass, an_episode(), 0)


async def test_a_dropped_connection_fails_the_episode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(AT, exc=TimeoutError())
    with pytest.raises(EpisodeFailedError):
        await read(hass, an_episode(), 0)


# -- files that are not MP3s: ffmpeg seeks in them, through this machine ------------

M4A = b"\x00\x00\x00\x20ftypM4A " + bytes(range(256)) * 300  # an MP4's first bytes, then filler
M4A_URL = "https://shows.example/one.m4a"
M4A_AT = f"https://{PUBLIC}/one.m4a"


async def _relay(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator, start_ms: int
) -> tuple[object, str, object]:
    """Open a non-MP3 episode and give back the client, the relay's path and the source."""
    assert await async_setup_component(hass, "http", {})
    hass.http.register_view(EpisodeRelayView(hass, "0.0.0"))
    client = await hass_client_no_auth()
    opener = episode_opener(hass, async_get_clientsession(hass), "0.0.0")
    source = await opener(an_episode(M4A_URL), start_ms)
    assert source.chunks is None
    assert source.url is not None
    assert source.url.startswith("http://127.0.0.1:")  # never the show's host
    return client, "/" + source.url.split("/", 3)[3], source


async def test_a_file_that_is_not_an_mp3_is_reached_through_this_machine_only(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    serve(aioclient_mock, M4A_AT, M4A)
    client, path, source = await _relay(hass, hass_client_no_auth, 600_000)
    assert source.seek_ms == 600_000  # type: ignore[attr-defined]

    # What ffmpeg does to seek: ask for a stretch from the middle.
    answer = await client.get(path, headers={"Range": "bytes=1000-1999"})  # type: ignore[attr-defined]
    assert answer.status == 206
    assert answer.headers["Content-Range"] == f"bytes 1000-1999/{len(M4A)}"
    assert await answer.read() == M4A[1000:2000]
    sent = aioclient_mock.mock_calls[-1][3]
    assert sent["Range"] == "bytes=1000-1999"
    assert sent["Host"] == "shows.example"  # audioUrl itself, each time
    assert sent["User-Agent"] == "HomeAssistant-Static/0.0.0"

    # Once ffmpeg is done, the address stops working.
    source.release()  # type: ignore[attr-defined]
    assert (await client.get(path)).status == 404  # type: ignore[attr-defined]


async def test_a_redirect_into_the_house_is_refused_by_the_relay_too(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The host answers honestly while it is looked over, then turns on the listener."""
    serve(aioclient_mock, M4A_AT, M4A)
    client, path, _ = await _relay(hass, hass_client_no_auth, 600_000)
    aioclient_mock.clear_requests()
    aioclient_mock.get(M4A_AT, status=302, headers={"Location": "http://192.168.1.1/admin"})

    answer = await client.get(path, headers={"Range": "bytes=0-99"})  # type: ignore[attr-defined]

    assert answer.status == 502
    assert "Location" not in answer.headers
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [
        M4A_AT
    ]  # the house was not asked


async def test_the_relay_knows_nothing_without_a_key(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    serve(aioclient_mock, M4A_AT, M4A)
    client, _, _ = await _relay(hass, hass_client_no_auth, 0)
    before = aioclient_mock.call_count
    assert (await client.get("/api/static_radio/episode/not-a-key")).status == 404  # type: ignore[attr-defined]
    assert aioclient_mock.call_count == before


@pytest.mark.parametrize(
    ("remote", "allowed"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("::ffff:127.0.0.1", True),
        ("192.168.1.20", False),  # the home network is not this machine
        ("172.30.32.2", False),
        ("93.184.216.34", False),
        ("", False),
        (None, False),
    ],
)
def test_only_this_machine_may_use_the_relay(remote: str | None, allowed: bool) -> None:
    assert _from_this_machine(SimpleNamespace(remote=remote)) is allowed  # type: ignore[arg-type]
