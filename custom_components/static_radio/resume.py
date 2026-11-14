"""Puts a speaker back on its station after it drops off the network.

A speaker can vanish for a while and come back: this project's own reference
speaker once left the network mid-stream, restarted itself and returned a
quarter of an hour later, silent. A radio that was on should be on again, so
this file watches each tuned speaker and tunes it again when it returns.

It only does so when all of these hold:

- The speaker was playing Static when it went, as far as can be told: it was
  tuned here and nobody has stopped it or played something else since.
- It came back within RESUME_WITHIN_S. After longer than that, sound starting
  by itself would be a surprise and not a recovery.
- It has been back for SETTLE_S, long enough to take a request.

The resume is a fresh tune, so it lands on the live moment like any other.
It opens without the spoken name: nobody asked for a change of station.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

_LOGGER = logging.getLogger(__name__)

RESUME_WITHIN_S = 600  # a speaker gone longer than this is not tuned again
SETTLE_S = 5  # how long a speaker has to have been back before it is asked
GRACE_S = 20  # after a tune, how long "not playing yet" is not taken for "stopped"

_GONE = (STATE_UNAVAILABLE, STATE_UNKNOWN)
_PLAYING = ("playing", "buffering")
_OURS = "Static "  # how every station's stream names itself (stream.py's icy-name)


class OnAir:
    """Whether one speaker is playing Static, and the watch that brings it back."""

    def __init__(
        self,
        hass: HomeAssistant,
        speaker: str,
        retune: Callable[[], Awaitable[None]],
        changed: Callable[[], None],
    ) -> None:
        self._hass = hass
        self._speaker = speaker
        self._retune = retune
        self._changed = changed
        self._tuned_at = 0.0
        self._gone_at: float | None = None
        self._pending: CALLBACK_TYPE | None = None
        self.on = False
        self._unwatch = async_track_state_change_event(hass, [speaker], self._speaker_changed)

    def tuned(self) -> None:
        """The speaker has just been handed a station."""
        self._tuned_at = time.monotonic()
        self._gone_at = None
        self._set(True)

    def stop(self) -> None:
        """Stop watching, for good."""
        self._unwatch()
        self._cancel()

    def _set(self, on: bool) -> None:
        if on != self.on:
            self.on = on
            self._changed()

    def _cancel(self) -> None:
        if self._pending is not None:
            self._pending()
            self._pending = None

    @callback
    def _speaker_changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        state = new.state if new is not None else STATE_UNAVAILABLE
        if state in _GONE:
            self._cancel()
            if self.on and self._gone_at is None:
                self._gone_at = time.monotonic()
            return
        if self._gone_at is not None:
            # Back. Ask it again if it was not gone long, once it has settled.
            gone_s = time.monotonic() - self._gone_at
            self._gone_at = None
            if gone_s <= RESUME_WITHIN_S:
                self._cancel()
                self._pending = async_call_later(self._hass, SETTLE_S, self._resume)
            else:
                self._set(False)
            return
        if time.monotonic() - self._tuned_at <= GRACE_S or self._pending is not None:
            return  # still starting, or about to be asked again
        title = new.attributes.get("media_title") if new is not None else None
        if state not in _PLAYING or (title and not str(title).startswith(_OURS)):
            self._set(False)  # someone stopped it, or put something else on

    @callback
    def _resume(self, _now: object) -> None:
        self._pending = None
        state = self._hass.states.get(self._speaker)
        if not self.on or state is None or state.state in _GONE:
            return
        self._hass.async_create_task(self._tune_again(), f"static_radio resume {self._speaker}")

    async def _tune_again(self) -> None:
        try:
            await self._retune()
        except HomeAssistantError as err:
            _LOGGER.warning("Could not put %s back on Static: %s", self._speaker, err)
            self._set(False)
