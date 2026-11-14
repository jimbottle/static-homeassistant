"""The station's name, spoken, to open a listen with.

A stream opens with a few seconds the speaker can start on at once while the
episode is fetched (segments.py). Rather than leave that silent, the station
is named in it, the same sentence the voice assistant answers with
(tuner.py): the owner's rule is that every change of station is announced
with the name as Static writes it.

The sentence is spoken in the voice the household's assistant already uses
(the preferred Assist pipeline's text-to-speech), or by Home Assistant's
default engine where there is no such pipeline. It is kept as samples for
as long as Home Assistant runs. It is this
integration's own sound, not a show's, so keeping it breaks no rule of
Static's. A listen never waits for it: a name not ready yet opens with
silence and is ready for the next one. With no text-to-speech set up, every
listen opens with silence.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .schedule import Station
from .station_audio import BYTES_PER_MS, CHANNELS, SAMPLE_RATE
from .tuner import tuned_reply

_LOGGER = logging.getLogger(__name__)

SPEAK_TIMEOUT_S = 30
MAX_OPENING_MS = 6000  # a sentence longer than this is not a station's name

_KEY = f"{DOMAIN}.openings"


async def _to_samples(ffmpeg: str, audio: bytes) -> bytes:
    """Any audio file's bytes as the stream's samples."""
    process = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", "pipe:0",
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    samples, _ = await process.communicate(audio)
    return samples


def _voice(hass: HomeAssistant) -> tuple[str | None, str | None, dict[str, str] | None]:
    """The engine, language and voice the household's assistant speaks with.

    That is the preferred Assist pipeline's. Without one, Home Assistant's
    default engine is used (None), which may be a different voice.
    """
    if "assist_pipeline" not in hass.config.components:
        return None, None, None
    from homeassistant.components.assist_pipeline.pipeline import (  # noqa: PLC0415
        async_get_pipeline,
    )

    try:
        pipeline = async_get_pipeline(hass)
    except HomeAssistantError, KeyError:
        return None, None, None
    if not pipeline.tts_engine:
        return None, None, None
    options = {"voice": pipeline.tts_voice} if pipeline.tts_voice else None
    return pipeline.tts_engine, pipeline.tts_language, options


async def _speak(hass: HomeAssistant, ffmpeg: str, station: Station) -> bytes:
    """The sentence as samples; empty when it cannot be spoken."""
    if "tts" not in hass.config.components:
        return b""
    # Imported here: text-to-speech is optional, and absent on many installs.
    from homeassistant.components import tts  # noqa: PLC0415

    try:
        async with asyncio.timeout(SPEAK_TIMEOUT_S):
            engine, language, options = _voice(hass)
            source = tts.generate_media_source_id(
                hass, tuned_reply(station), engine=engine, language=language, options=options
            )
            _, audio = await tts.async_get_media_source_audio(hass, source)
            samples = await _to_samples(ffmpeg, audio)
    except (HomeAssistantError, TimeoutError, OSError, ValueError) as err:
        _LOGGER.warning("Could not speak the name of %s: %s", station.display_name, err)
        return b""
    if len(samples) > MAX_OPENING_MS * BYTES_PER_MS:
        return b""
    return samples


def opening_for(hass: HomeAssistant, ffmpeg: str, station: Station) -> bytes:
    """The spoken opening for a station if it is ready, else silence for now."""
    ready: dict[str, bytes | asyncio.Task[bytes]] = hass.data.setdefault(_KEY, {})
    name = station.display_name
    have = ready.get(name)
    if isinstance(have, bytes):
        return have
    if have is not None and not have.done():
        return b""  # being spoken now

    # Never tried, or tried and failed: try (again), without waiting for it.
    async def make() -> bytes:
        samples = await _speak(hass, ffmpeg, station)
        if samples:
            ready[name] = samples
        return samples

    task = hass.async_create_background_task(make(), f"{DOMAIN} opening {name}")
    # Home Assistant starts a task at once, so it may already be over.
    if not task.done():
        ready[name] = task
    have = ready.get(name)
    return have if isinstance(have, bytes) else b""
