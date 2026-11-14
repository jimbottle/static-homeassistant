"""An episode's bytes, from the show's own host, starting at the live point.

Static's rule is that every listen is a normal download from the show's own
host, so the show's own stats count it. This file is where that download is
asked for, and it follows the guide's "Playing from the middle":

- audioUrl itself is requested every time, never the address it redirects to,
  and its redirects are followed as they come.
- A listen that starts partway through sends "Range: bytes=N-". For an MP3, N
  comes from the file's own header (mp3seek.py), read with one or two small
  ranged requests first, as the reference player's MP3Seek.pm does.
- With a second or less to skip, an MP3 starts at the top without asking.
- A file that is not an MP3 (MP4, M4A, WAV) has to be seeked in by something
  that understands it, which is ffmpeg: an MP4 keeps the index it cannot be
  played without anywhere in the file, often at the end. ffmpeg makes its own
  Range requests, and it makes them to this machine: the relay at the bottom
  of this file passes each one on to audioUrl and the answer back.
- A host with no ranges is played from the top, as the guide says.

The host belongs to a third party and can redirect anywhere, and Home
Assistant sits inside a home network. So every request made here, the relay's
included, goes through one function that holds each hop to the same rule as a
feed (feed.py): web addresses only, public addresses only, and the connection
goes to the address that was checked. ffmpeg is never given a show's address
and never follows a redirect, because it would check neither.

Nothing is kept. Bytes are passed on as they arrive and no more is read than
the speaker takes, so the download runs at about the pace of the listening.
"""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import AsyncIterator, Mapping
from http import HTTPStatus

import aiohttp
from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import HomeAssistantView
from yarl import URL

from .api import request_headers
from .const import DOMAIN
from .feed import FeedRefusedError, public_address
from .mp3seek import HEAD_BYTES, Mp3Info, RangeRequest, byte_for, first_frame, plan_probe
from .schedule import Episode
from .station_audio import EpisodeFailedError, OpenEpisode, Source

MIN_SEEK_MS = 1000  # the reference starts at the top with a second or less to skip
MAX_REDIRECTS = 8  # a show's measurement services can add several hops
CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 30  # between two reads; a whole episode takes as long as it plays
RELAY_URL = "/api/static_radio/episode/{key}"

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_OK = 200
_PARTIAL = 206
_CHUNK_BYTES = 32 * 1024
_MP3_NAMES = (".mp3",)
_FAILURES = (aiohttp.ClientError, TimeoutError, FeedRefusedError, OSError)
_TIMEOUT = aiohttp.ClientTimeout(
    total=None, sock_connect=CONNECT_TIMEOUT_S, sock_read=READ_TIMEOUT_S
)
_RELAYS_KEY = f"{DOMAIN}.episode_relays"  # key -> the audioUrl it stands for
# What of a host's answer ffmpeg needs in order to seek.
_PASSED_ON = ("Content-Type", "Content-Range", "Content-Length", "Accept-Ranges")


def _total_bytes(response: aiohttp.ClientResponse) -> int | None:
    """The file's size, from a ranged answer's Content-Range."""
    _, _, total = response.headers.get("Content-Range", "").rpartition("/")
    return int(total) if total.isdigit() else None


async def _get(
    hass: HomeAssistant, session: aiohttp.ClientSession, url: str, headers: Mapping[str, str]
) -> aiohttp.ClientResponse:
    """Ask url, following its redirects by hand, each hop checked. The caller releases it."""
    target = URL(url)
    for _ in range(MAX_REDIRECTS + 1):
        if target.scheme not in ("http", "https") or not target.host or target.user:
            raise FeedRefusedError("not a plain web address")
        address = await public_address(hass, target.host)
        named = target.host if target.is_default_port() else f"{target.host}:{target.port}"
        response = await session.get(
            target.with_host(address),
            headers={**headers, "Host": named},
            allow_redirects=False,
            server_hostname=target.host if target.scheme == "https" else None,
            timeout=_TIMEOUT,
        )
        location = response.headers.get("Location")
        if response.status not in _REDIRECTS or not location:
            return response
        response.release()
        target = target.join(URL(location))
    raise FeedRefusedError("too many redirects")


class _Host:
    """One show's host, asked the way the rules at the top of this file say."""

    def __init__(self, hass: HomeAssistant, session: aiohttp.ClientSession, version: str) -> None:
        self._hass = hass
        self._session = session
        self._headers = request_headers(version)

    async def get(self, url: str, headers: Mapping[str, str]) -> aiohttp.ClientResponse:
        return await _get(self._hass, self._session, url, {**self._headers, **headers})

    async def ranged(self, url: str, start: int, length: int) -> tuple[bytes, int | None] | None:
        """Those bytes of the file and its size, or None from a host without ranges."""
        response = await self.get(url, {"Range": f"bytes={start}-{start + length - 1}"})
        try:
            if response.status != _PARTIAL:
                return None
            # One read returns what has arrived so far, which over a real
            # network is often less than was asked for.
            data = b""
            while len(data) < length and (more := await response.content.read(length - len(data))):
                data += more
            return data, _total_bytes(response)
        finally:
            response.release()

    async def chunks(self, url: str, at: int) -> AsyncIterator[bytes]:
        """The file's bytes from byte `at`, as they arrive."""
        response = None
        try:
            response = await self.get(url, {"Range": f"bytes={at}-"} if at else {})
            if response.status not in (_OK, _PARTIAL):
                raise EpisodeFailedError(f"the host answered {response.status}")
            async for chunk in response.content.iter_chunked(_CHUNK_BYTES):
                yield chunk
        except _FAILURES as err:
            raise EpisodeFailedError(str(err) or type(err).__name__) from err
        finally:
            if response is not None:
                response.close()

    async def mp3_byte(self, episode: Episode, head: bytes, total: int | None, at_ms: int) -> int:
        """Where in an MP3 at_ms is; 0 when it has to start at the top."""
        info: Mp3Info | RangeRequest | None = plan_probe(head, total)
        if isinstance(info, RangeRequest):
            frames = await self.ranged(episode.audio_url, info.start, info.length)
            info = first_frame(frames[0], info.start, total) if frames else None
        if info is None or at_ms <= MIN_SEEK_MS:
            return 0
        return byte_for(info, at_ms / 1000, episode.duration_ms / 1000)

    def relayed(self, url: str, seek_ms: int) -> Source:
        """An address on this machine that stands for url, for ffmpeg to seek in."""
        http = self._hass.http
        if http is None:
            raise EpisodeFailedError("Home Assistant's web server is not running")
        relays: dict[str, str] = self._hass.data.setdefault(_RELAYS_KEY, {})
        key = secrets.token_urlsafe(24)
        relays[key] = url
        scheme = "https" if http.ssl_certificate else "http"
        local = f"{scheme}://127.0.0.1:{http.server_port}{RELAY_URL.format(key=key)}"

        def release() -> None:
            relays.pop(key, None)

        return Source(url=local, seek_ms=seek_ms, release=release)

    async def open(self, episode: Episode, start_ms: int) -> Source:
        url = episode.audio_url
        named_mp3 = URL(url).path.lower().endswith(_MP3_NAMES)
        if named_mp3 and start_ms <= MIN_SEEK_MS:
            return Source(chunks=self.chunks(url, 0))
        try:
            head = await self.ranged(url, 0, HEAD_BYTES)
            if head is None:  # no ranges: from the top
                return Source(chunks=self.chunks(url, 0))
            data, total = head
            if data.startswith(b"ID3") or plan_probe(data, total) is not None:
                at = await self.mp3_byte(episode, data, total, start_ms)
                return Source(chunks=self.chunks(url, at))
        except _FAILURES as err:
            raise EpisodeFailedError(str(err) or type(err).__name__) from err
        return self.relayed(url, start_ms if start_ms > MIN_SEEK_MS else 0)


def episode_opener(
    hass: HomeAssistant, session: aiohttp.ClientSession, version: str
) -> OpenEpisode:
    """The one way an episode is asked for, with the rules at the top of this file."""
    return _Host(hass, session, version).open


def _from_this_machine(request: web.Request) -> bool:
    try:
        address = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


class EpisodeRelayView(HomeAssistantView):
    """Passes ffmpeg's requests for one episode on to its audioUrl, checked.

    Only this machine may ask, only with a key handed out for one episode
    being decoded right now, and the answer is never a redirect: the checking
    function follows those itself, and anything it will not follow becomes an
    error here.
    """

    requires_auth = False
    url = RELAY_URL
    name = "api:static_radio:episode"

    def __init__(self, hass: HomeAssistant, version: str) -> None:
        self._hass = hass
        self._headers = request_headers(version)

    async def get(self, request: web.Request, key: str) -> web.StreamResponse:
        relays: dict[str, str] = self._hass.data.get(_RELAYS_KEY, {})
        url = relays.get(key)
        if url is None or not _from_this_machine(request):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        asked = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
        try:
            upstream = await _get(
                self._hass, async_get_clientsession(self._hass), url, {**self._headers, **asked}
            )
        except _FAILURES:
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        try:
            if upstream.status in _REDIRECTS:
                return web.Response(status=HTTPStatus.BAD_GATEWAY)
            response = web.StreamResponse(
                status=upstream.status,
                headers={k: upstream.headers[k] for k in _PASSED_ON if k in upstream.headers},
            )
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(_CHUNK_BYTES):
                await response.write(chunk)
            return response
        except (*_FAILURES, ConnectionError):
            return web.Response(status=HTTPStatus.BAD_GATEWAY)
        finally:
            upstream.close()
