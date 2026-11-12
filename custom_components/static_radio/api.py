"""The two things this integration asks Static for: the schedule and the counts.

Static's developer guide (https://staticradio.app/developers) sets the terms,
and this file is where they are kept:

- Only the public interface: schedule-wear.json and /api/counts, with GET.
  Nothing else at Static is requested from anywhere in this integration.
- The schedule at most every 15 minutes, with If-None-Match, so an unchanged
  file comes back as a small 304. The counts at most once a minute.
- A User-Agent that names the app and its version on every request. Python's
  default one gets a 403 from the schedule's host.

It does no I/O of its own and imports nothing from Home Assistant: the caller
passes in a function that performs one GET, so the rules can be tested without
a network. It hands back the decoded JSON; schedule.py turns that into a
timeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

SCHEDULE_URL = "https://data.staticradio.app/schedule-wear.json"
COUNTS_URL = "https://staticradio.app/api/counts"

SCHEDULE_MIN_INTERVAL_S = 15 * 60  # the guide's floor, and Schedule.pm's REFRESH_SECONDS
COUNTS_MIN_INTERVAL_S = 60

HTTP_OK = 200
HTTP_NOT_MODIFIED = 304


@dataclass(frozen=True, slots=True)
class Response:
    """What came back from one GET."""

    status: int
    headers: Mapping[str, str]
    body: bytes  # already decompressed

    def header(self, name: str) -> str | None:
        """A header's value, whatever case the server sent its name in."""
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None


# Performs one GET of a URL with the given headers. It must follow redirects
# and must not add, drop or rewrite the headers it is given.
Fetch = Callable[[str, Mapping[str, str]], Awaitable[Response]]


def user_agent(version: str) -> str:
    """The User-Agent every request to Static and to a show's host carries."""
    return f"HomeAssistant-Static/{version}"


def request_headers(version: str) -> dict[str, str]:
    """The headers every outbound request carries.

    Accept-Language is set because at least one large podcast host answers a
    wildcard or missing value with a 404.
    """
    return {"User-Agent": user_agent(version), "Accept-Language": "en"}


class ScheduleClient:
    """Keeps one copy of the schedule and asks for a new one politely."""

    def __init__(
        self, fetch: Fetch, version: str, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._fetch = fetch
        self._headers = request_headers(version)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._asked_at: float | None = None
        self._etag: str | None = None
        self._raw: dict[str, Any] | None = None

    @property
    def raw(self) -> dict[str, Any] | None:
        """The copy in hand, without asking."""
        return self._raw

    async def get(self) -> dict[str, Any] | None:
        """The decoded schedule, fetched only if 15 minutes have passed.

        The floor counts from the last attempt, successful or not, so a host
        that is failing is not asked more often than one that is well. When a
        fetch fails the copy in hand is kept, and None is returned only if
        there has never been one.
        """
        async with self._lock:
            now = self._clock()
            if self._asked_at is not None and now - self._asked_at < SCHEDULE_MIN_INTERVAL_S:
                return self._raw
            self._asked_at = now

            headers = dict(self._headers)
            if self._etag and self._raw is not None:
                headers["If-None-Match"] = self._etag
            try:
                response = await self._fetch(SCHEDULE_URL, headers)
            except Exception as err:
                _LOGGER.warning("schedule: fetch failed: %s", err)
                return self._raw

            fresh = _decode_schedule(response)
            if fresh is not None:
                self._raw = fresh
                self._etag = response.header("ETag")
            return self._raw


def _decode_schedule(response: Response) -> dict[str, Any] | None:
    """The new schedule in a response, or None to keep the copy in hand."""
    if response.status == HTTP_NOT_MODIFIED:
        return None
    if response.status != HTTP_OK:
        _LOGGER.warning("schedule: HTTP %d", response.status)
        return None
    try:
        fresh = json.loads(response.body)
    except ValueError as err:
        _LOGGER.warning("schedule: could not read it: %s", err)
        return None
    if not isinstance(fresh, dict):
        _LOGGER.warning("schedule: not a JSON object")
        return None
    return fresh


class CountsClient:
    """Listeners per station, asked for at most once a minute."""

    def __init__(
        self, fetch: Fetch, version: str, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._fetch = fetch
        self._headers = request_headers(version)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._asked_at: float | None = None
        self._counts: dict[str, int | None] = {}

    async def get(self) -> dict[str, int | None]:
        """Listeners by station id; None where Static does not know.

        Call this only while something is displaying the numbers. A failed
        fetch keeps the last ones.
        """
        async with self._lock:
            now = self._clock()
            if self._asked_at is not None and now - self._asked_at < COUNTS_MIN_INTERVAL_S:
                return dict(self._counts)
            self._asked_at = now
            try:
                response = await self._fetch(COUNTS_URL, dict(self._headers))
                if response.status != HTTP_OK:
                    _LOGGER.debug("counts: HTTP %d", response.status)
                    return dict(self._counts)
                counts = json.loads(response.body).get("counts")
            except Exception as err:
                _LOGGER.debug("counts: fetch failed: %s", err)
                return dict(self._counts)
            if isinstance(counts, dict):
                self._counts = {
                    str(station): value if isinstance(value, int) else None
                    for station, value in counts.items()
                }
            return dict(self._counts)
