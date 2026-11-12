"""The Support link for the show that is playing.

Static asks every player to show a link labeled "Support" to the show's own
support page while the show plays, and never on a bulletin
(https://staticradio.app/developers#support). The link is the episode's
supportUrl. Schedule files from before 2026-11-11 carry no supportUrl at all;
for those, and only those, the guide says to read it from the show's RSS feed:
the first channel-level <podcast:funding url="..."> tag, fetching each feed at
most once a day.

A port of the guide's support_url_from_feed(). Like api.py it does no I/O of
its own and imports nothing from Home Assistant: the caller passes in the
function that performs one GET.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from .api import HTTP_OK, Fetch, request_headers
from .schedule import Episode

_LOGGER = logging.getLogger(__name__)

FEED_MIN_INTERVAL_S = 24 * 3600  # the guide's floor: each feed at most once a day

# A feed is somebody else's text, and can be many megabytes. The channel's own
# tags come before the first episode, so only the start of the file is read,
# and the pattern below is the guide's with a limit on every run of characters,
# so no feed can make it slow.
CHANNEL_MAX_BYTES = 256 * 1024
_FUNDING = re.compile(
    r"""<podcast:funding\b[^<>]{0,2000}?\burl\s{0,20}=\s{0,20}["']([^"'<>]{1,2048})["']"""
)
_FIRST_ITEM = re.compile(r"<item[\s>]")
_WEB = ("https://", "http://")


def has_support_urls(raw_schedule: dict[str, Any]) -> bool:
    """True when the schedule file carries Support links itself.

    One episode with the field is enough: then a show without it has no
    support page, and its feed is not to be asked.
    """
    categories = raw_schedule.get("categories") or {}
    return any(
        "supportUrl" in episode
        for station in categories.values()
        for episode in station.get("episodes") or []
    )


def funding_url(feed_text: str) -> str | None:
    """The show's support page from its RSS feed, or None.

    Only the channel's own tag counts, which is the one before the first
    episode. Anything that is not a web address is dropped, because this ends
    up as a link a listener taps.
    """
    channel = _FIRST_ITEM.split(feed_text, maxsplit=1)[0]
    found = _FUNDING.search(channel)
    if found is None:
        return None
    url = html.unescape(found.group(1).strip())
    return url if url.lower().startswith(_WEB) else None


class SupportLinks:
    """Answers "what is this show's Support link", asking a feed at most daily."""

    def __init__(
        self, fetch: Fetch, version: str, clock: Callable[[], float] | None = None
    ) -> None:
        self._fetch = fetch
        self._headers = request_headers(version)
        self._clock = clock if clock is not None else time.monotonic
        self._feeds: dict[str, tuple[str | None, float]] = {}  # feed_url -> (link, asked at)
        self._asking: dict[str, asyncio.Lock] = {}  # one per feed being asked right now

    async def for_episode(self, episode: Episode, schedule_has_links: bool) -> str | None:
        """The link to show beside this episode, or None for no link.

        schedule_has_links is has_support_urls() of the schedule the episode
        came from. A bulletin never has a link, whatever its fields say.
        """
        if episode.is_bulletin:
            return None
        if episode.support_url:
            return episode.support_url
        if schedule_has_links or not episode.feed_url.lower().startswith(_WEB):
            return None
        return await self._from_feed(episode.feed_url)

    def _known(self, feed_url: str, now: float) -> tuple[str | None] | None:
        """Today's answer for a feed, wrapped so that "no link" is not "not asked"."""
        known = self._feeds.get(feed_url)
        if known is not None and now - known[1] < FEED_MIN_INTERVAL_S:
            return (known[0],)
        return None

    async def _from_feed(self, feed_url: str) -> str | None:
        if (known := self._known(feed_url, self._clock())) is not None:
            return known[0]
        # One lock per feed: two lookups of the same show share one request,
        # and a slow host holds up nobody else's.
        async with self._asking.setdefault(feed_url, asyncio.Lock()):
            now = self._clock()
            if (known := self._known(feed_url, now)) is not None:
                return known[0]

            link: str | None = None
            try:
                response = await self._fetch(feed_url, dict(self._headers))
                if response.status == HTTP_OK:
                    start = response.body[:CHANNEL_MAX_BYTES]
                    link = funding_url(start.decode("utf-8", "replace"))
                else:
                    _LOGGER.debug("support: feed answered HTTP %d", response.status)
            except Exception as err:
                _LOGGER.debug("support: feed fetch failed: %s", err)

            # A failure is remembered for the day too: no link for now, and
            # the show's host is not asked again on the next episode change.
            # Feeds that have left the schedule are dropped as they age out.
            now = self._clock()
            self._feeds = {
                url: entry
                for url, entry in self._feeds.items()
                if now - entry[1] < FEED_MIN_INTERVAL_S
            }
            self._feeds[feed_url] = (link, now)
            self._asking = {
                url: lock
                for url, lock in self._asking.items()
                if url in self._feeds or lock.locked()
            }
            return link
