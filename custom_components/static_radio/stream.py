"""A station's stream, served to the speaker that is tuned to it.

This is the one place a speaker gets Static's sound from. A speaker is handed
an address here (stream_path), and for as long as it keeps reading from it,
it is sent one endless FLAC stream of the station: station_audio.py makes the
samples, cut to Static's timeline, and one ffmpeg encodes them. The model is
the ffmpeg proxy view in Home Assistant's own ESPHome integration.

What this file is careful about:

- No sign-in, because a speaker cannot sign in. So the address holds a long
  random key, one per speaker, and a caller from outside the home network is
  refused whatever it holds.
- One stream per speaker. A new connection with a speaker's key ends the one
  before it.
- Every connection starts at the live moment and the stream never says how
  long it is, so a speaker that stops and starts again lands on live.
- An episode is asked of its show's host only once the connection has taken
  the stream's first bytes and stayed. A player looking a new address over
  before it plays opens and drops a connection in well under that time, and
  that must not count as a listen at the show's host.
- A reader that stops reading is dropped, so a paused speaker has to
  reconnect, and reconnecting lands on live.
- The "icy-name" header carries the station's name. Music Assistant reads it
  as the sign of a radio station, which is what stops it offering to seek.

Nothing is written to disk: samples go from ffmpeg's pipe to the socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import secrets
from http import HTTPStatus
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.http import HomeAssistantView
from homeassistant.loader import async_get_integration

from .const import CONF_ANNOUNCE, CONF_LEADS_MS, DEFAULT_LEAD_MS, DOMAIN
from .coordinator import now_ms
from .episode import EpisodeRelayView, episode_opener
from .opening import opening_for
from .schedule import Episode
from .station_audio import CHANNELS, SAMPLE_RATE, Listen, OpenEpisode, Source, station_pcm

if TYPE_CHECKING:
    from . import StaticConfigEntry

_LOGGER = logging.getLogger(__name__)

CONF_STREAM_KEYS = "stream_keys"  # in the entry's data: speaker entity id -> its key
STREAM_URL = "/api/static_radio/stream/{key}/{filename}"
SETTLE_S = 0.3  # how long a connection must stay before an episode is asked for
STALL_S = 20  # a reader that takes nothing for this long is dropped
QUIET = "quiet"  # the query word that leaves the spoken opening out

_READ_BYTES = 16 * 1024
_VIEW_KEY = f"{DOMAIN}.stream_view"


def stream_path(
    hass: HomeAssistant, entry: StaticConfigEntry, speaker: str, station_id: str, *, say: bool
) -> str:
    """The address a speaker is given for a station, without the host.

    say is whether the stream may open with the station's name, spoken. A tune
    asked for by voice passes False: the assistant has just said it.
    """
    keys: dict[str, str] = dict(entry.data.get(CONF_STREAM_KEYS, {}))
    if speaker not in keys:
        keys[speaker] = secrets.token_urlsafe(24)
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_STREAM_KEYS: keys})
    path = STREAM_URL.format(key=keys[speaker], filename=f"{station_id}.flac")
    return path if say else f"{path}?{QUIET}=1"


def _from_home(request: web.Request) -> bool:
    """True for a caller on this machine or the home network."""
    try:
        address = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_private or address.is_link_local


def _ffmpeg_binary(hass: HomeAssistant) -> str:
    """The ffmpeg Home Assistant is set to use, or the one on its path."""
    if "ffmpeg" in hass.config.components:
        # Imported here: its own requirement is only installed where it is set up.
        from homeassistant.components import ffmpeg  # noqa: PLC0415

        return str(ffmpeg.get_ffmpeg_manager(hass).binary)
    return "ffmpeg"


def _encode_command(binary: str) -> list[str]:
    return [
        binary, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
        "-c:a", "flac", "-compression_level", "2", "-flush_packets", "1",
        "-f", "flac", "pipe:1",
    ]  # fmt: skip


def _held(opener: OpenEpisode, settled: asyncio.Event) -> OpenEpisode:
    """The same opener, asking nothing of a show's host until the connection has settled."""

    async def open_episode(episode: Episode, start_ms: int) -> Source:
        await settled.wait()
        return await opener(episode, start_ms)

    return open_episode


class StationStreamView(HomeAssistantView):
    """Serves each tuned speaker its station."""

    requires_auth = False
    url = STREAM_URL
    name = "api:static_radio:stream"

    def __init__(self, hass: HomeAssistant, version: str) -> None:
        self._hass = hass
        self._version = version
        self._serving: dict[str, asyncio.Task[object]] = {}  # speaker -> its connection

    def _entry(self) -> StaticConfigEntry | None:
        entries = self._hass.config_entries.async_loaded_entries(DOMAIN)
        return entries[0] if entries else None

    async def get(self, request: web.Request, key: str, filename: str) -> web.StreamResponse:
        """One listen: from the live moment, until the speaker stops reading."""
        entry = self._entry()
        if entry is None or not _from_home(request):
            return web.Response(status=HTTPStatus.NOT_FOUND)
        keys: dict[str, str] = entry.data.get(CONF_STREAM_KEYS, {})
        speaker = next(
            (who for who, theirs in keys.items() if secrets.compare_digest(theirs, key)), None
        )
        station_id = filename.removesuffix(".flac")
        coordinator = entry.runtime_data
        station = coordinator.data.station(station_id) if coordinator.data else None
        if speaker is None or station is None or not filename.endswith(".flac"):
            return web.Response(status=HTTPStatus.NOT_FOUND)

        # One stream per speaker: this connection replaces the one before it.
        before = self._serving.get(speaker)
        if before is not None and not before.done():
            before.cancel()
        mine = asyncio.current_task()
        if mine is not None:
            self._serving[speaker] = mine

        binary = _ffmpeg_binary(self._hass)
        name = station.display_name
        say = entry.options.get(CONF_ANNOUNCE, True) and QUIET not in request.query
        settled = asyncio.Event()
        listen = Listen(
            station_id=station_id,
            lead_ms=int(entry.options.get(CONF_LEADS_MS, {}).get(speaker, DEFAULT_LEAD_MS)),
            now_ms=now_ms,
            schedule=lambda: coordinator.data,
            open_episode=_held(
                episode_opener(self._hass, async_get_clientsession(self._hass), self._version),
                settled,
            ),
            ffmpeg=binary,
            opening=opening_for(self._hass, binary, station) if say else b"",
        )

        response = web.StreamResponse(
            headers={
                "Content-Type": "audio/flac",
                "Cache-Control": "no-store",
                "icy-name": f"Static {name}".encode("ascii", "replace").decode("ascii"),
            }
        )
        await response.prepare(request)
        try:
            await self._serve(listen, response, settled)
        except ConnectionResetError, ConnectionError, TimeoutError:
            pass  # the speaker stopped listening, or stopped reading
        finally:
            if self._serving.get(speaker) is mine:
                del self._serving[speaker]
        return response

    async def _serve(
        self, listen: Listen, response: web.StreamResponse, settled: asyncio.Event
    ) -> None:
        """Samples into the encoder, and the encoder's output to the speaker."""
        encoder = await asyncio.create_subprocess_exec(
            *_encode_command(listen.ffmpeg),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert encoder.stdin is not None and encoder.stdout is not None  # noqa: S101
        samples = station_pcm(listen)

        async def feed() -> None:
            assert encoder.stdin is not None  # noqa: S101
            try:
                async for chunk in samples:
                    encoder.stdin.write(chunk)
                    await encoder.stdin.drain()
            except BrokenPipeError, ConnectionResetError:
                pass  # the encoder has gone: the speaker stopped listening
            except Exception:
                _LOGGER.exception("The stream for %s stopped", listen.station_id)
            finally:
                # With its input closed the encoder ends, and so does the
                # connection: a player that is still there connects again.
                with contextlib.suppress(OSError):
                    encoder.stdin.close()

        feeding = asyncio.create_task(feed())
        settle: asyncio.TimerHandle | None = None
        try:
            while data := await encoder.stdout.read(_READ_BYTES):
                async with asyncio.timeout(STALL_S):
                    await response.write(data)
                if settle is None:
                    settle = self._hass.loop.call_later(SETTLE_S, settled.set)
        finally:
            if settle is not None:
                settle.cancel()
            feeding.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feeding
            await samples.aclose()
            if encoder.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    encoder.kill()
                await encoder.wait()


def prepare_openings(hass: HomeAssistant, entry: StaticConfigEntry) -> None:
    """Have every station's name spoken ahead of time, so no first listen opens silent."""
    if not entry.options.get(CONF_ANNOUNCE, True) or entry.runtime_data.data is None:
        return
    binary = _ffmpeg_binary(hass)
    for station in entry.runtime_data.data.stations:
        opening_for(hass, binary, station)


async def async_setup_stream(hass: HomeAssistant) -> None:
    """Register the stream's address, once for the life of Home Assistant."""
    if _VIEW_KEY in hass.data:
        return
    integration = await async_get_integration(hass, DOMAIN)
    hass.data[_VIEW_KEY] = True
    hass.http.register_view(StationStreamView(hass, str(integration.version)))
    hass.http.register_view(EpisodeRelayView(hass, str(integration.version)))
