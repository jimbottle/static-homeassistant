"""support.py finds the Support link the way Static's guide says to.

No network: each test hands it a fake GET and a clock it controls. The feed
text is written here, in the shape of a real podcast feed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from custom_components.static_radio.api import Response
from custom_components.static_radio.schedule import Episode, parse_schedule
from custom_components.static_radio.support import (
    CHANNEL_MAX_BYTES,
    FEED_MIN_INTERVAL_S,
    SupportLinks,
    funding_url,
    has_support_urls,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/schedule-wear.2026-11-11.json").read_text(encoding="utf-8")
)
FEED_URL = "https://example.com/feed.xml"
FEED = """<?xml version="1.0"?>
<rss xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel>
<title>A Show</title>
<podcast:funding url="https://example.com/support?a=1&amp;b=2">Buy us a coffee</podcast:funding>
<podcast:funding url="https://example.com/second">Second</podcast:funding>
<item><title>One</title>
<podcast:funding url="https://example.com/episode-only">Episode</podcast:funding></item>
</channel></rss>"""


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeHost:
    """Records every request and answers from a queue."""

    def __init__(self, *answers: Response | Exception) -> None:
        self.answers = list(answers)
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def fetch(self, url: str, headers: Mapping[str, str]) -> Response:
        self.requests.append((url, dict(headers)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _feed(text: str = FEED) -> Response:
    return Response(200, {}, text.encode())


def _episode(**changes: object) -> Episode:
    base = Episode(
        episode_id="e1",
        audio_url="https://example.com/e1.mp3",
        duration_ms=60_000,
        title="One",
        feed_title="A Show",
        episode_url="https://example.com/e1",
        feed_url=FEED_URL,
        image_url="",
        gain_db=0.0,
        is_bulletin=False,
        support_url=None,
        placement_id=None,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _links(host: FakeHost, clock: Clock | None = None) -> SupportLinks:
    return SupportLinks(host.fetch, "0.1.0", clock or Clock())


# -- reading the tag ----------------------------------------------------------


def test_the_first_channel_level_tag_is_the_link() -> None:
    assert funding_url(FEED) == "https://example.com/support?a=1&b=2"


def test_a_tag_inside_an_episode_does_not_count() -> None:
    feed = '<channel><item><podcast:funding url="https://example.com/x"/></item></channel>'
    assert funding_url(feed) is None


def test_a_feed_without_the_tag_has_no_link() -> None:
    assert funding_url("<rss><channel><title>A Show</title></channel></rss>") is None


def test_single_quotes_and_spaces_are_read() -> None:
    feed = "<channel><podcast:funding  url = ' https://example.com/tip ' >Tip</podcast:funding>"
    assert funding_url(feed) == "https://example.com/tip"


def test_a_link_that_is_not_a_web_address_is_dropped() -> None:
    feed = '<channel><podcast:funding url="javascript:alert(1)">x</podcast:funding></channel>'
    assert funding_url(feed) is None


def test_a_hostile_feed_is_read_quickly_and_yields_nothing() -> None:
    # Thousands of unclosed tags: the pattern's limits keep this linear.
    feed = "<channel>" + "<podcast:funding " * 20_000 + "x" * 200_000
    assert funding_url(feed) is None
    assert funding_url('<channel><podcast:funding url="https://e.com/' + "a" * 5000 + '">') is None


# -- does the schedule carry links itself -------------------------------------


def test_the_saved_schedule_has_no_support_links() -> None:
    # The 2026-11-11 file predates the field, which is why the fallback exists.
    assert has_support_urls(FIXTURE) is False


def test_one_episode_with_the_field_is_enough() -> None:
    raw = {
        "categories": {
            "a": {"episodes": [{"title": "x"}, {"title": "y", "supportUrl": "https://e.com/s"}]},
            "b": {"episodes": []},
        }
    }
    assert has_support_urls(raw) is True
    assert has_support_urls({"categories": {}}) is False
    assert has_support_urls({}) is False


# -- choosing the link for an episode -----------------------------------------


def test_the_schedules_own_link_wins_and_no_feed_is_asked() -> None:
    host = FakeHost()
    episode = _episode(support_url="https://example.com/from-schedule")
    link = asyncio.run(_links(host).for_episode(episode, schedule_has_links=True))
    assert link == "https://example.com/from-schedule"
    assert host.requests == []


def test_a_bulletin_never_has_a_link() -> None:
    host = FakeHost()
    links = _links(host)
    bulletin = _episode(is_bulletin=True, support_url="https://example.com/should-not-show")
    assert asyncio.run(links.for_episode(bulletin, schedule_has_links=False)) is None
    assert asyncio.run(links.for_episode(bulletin, schedule_has_links=True)) is None
    assert host.requests == []


def test_no_feed_is_asked_when_the_schedule_carries_links() -> None:
    host = FakeHost()
    assert asyncio.run(_links(host).for_episode(_episode(), schedule_has_links=True)) is None
    assert host.requests == []


def test_an_older_schedule_falls_back_to_the_feed() -> None:
    host = FakeHost(_feed())
    link = asyncio.run(_links(host).for_episode(_episode(), schedule_has_links=False))
    assert link == "https://example.com/support?a=1&b=2"
    assert host.requests == [
        (FEED_URL, {"User-Agent": "HomeAssistant-Static/0.1.0", "Accept-Language": "en"})
    ]


def test_an_episode_without_a_feed_asks_nothing() -> None:
    host = FakeHost()
    episode = _episode(feed_url="")
    assert asyncio.run(_links(host).for_episode(episode, schedule_has_links=False)) is None
    assert host.requests == []


def test_only_web_feeds_are_asked() -> None:
    host = FakeHost()
    links = _links(host)
    for feed_url in ("file:///etc/hosts", "ftp://example.com/feed.xml", "feed.xml"):
        episode = _episode(feed_url=feed_url)
        assert asyncio.run(links.for_episode(episode, schedule_has_links=False)) is None
    assert host.requests == []


def test_only_the_start_of_a_feed_is_read() -> None:
    late = "<channel>" + " " * CHANNEL_MAX_BYTES + '<podcast:funding url="https://e.com/s"/>'
    host = FakeHost(_feed(late))
    assert asyncio.run(_links(host).for_episode(_episode(), False)) is None


def test_a_slow_feed_holds_up_no_other_show() -> None:
    other = _episode(feed_url="https://example.org/other.xml")

    async def run() -> tuple[str | None, str | None, int]:
        release = asyncio.Event()
        asked: list[str] = []

        async def fetch(url: str, headers: Mapping[str, str]) -> Response:
            asked.append(url)
            if url == FEED_URL:
                await release.wait()
            return _feed()

        links = SupportLinks(fetch, "0.1.0", Clock())
        slow_one = asyncio.create_task(links.for_episode(_episode(), False))
        slow_two = asyncio.create_task(links.for_episode(_episode(episode_id="e2"), False))
        await asyncio.sleep(0)
        quick = await asyncio.wait_for(links.for_episode(other, False), timeout=1)
        release.set()
        first, second = await slow_one, await slow_two
        assert first == second
        return quick, first, asked.count(FEED_URL)

    quick, slow, times_asked = asyncio.run(run())
    assert quick == slow == "https://example.com/support?a=1&b=2"
    assert times_asked == 1  # two lookups of one show shared one request


# -- at most once a day per feed ----------------------------------------------


def test_a_feed_is_asked_at_most_once_a_day() -> None:
    clock = Clock()
    host = FakeHost(_feed(), _feed("<channel></channel>"))
    links = _links(host, clock)

    async def run() -> list[str | None]:
        seen = [await links.for_episode(_episode(), False)]
        clock.now += FEED_MIN_INTERVAL_S - 1
        seen.append(await links.for_episode(_episode(episode_id="e2"), False))
        clock.now += 1
        seen.append(await links.for_episode(_episode(), False))
        return seen

    seen = asyncio.run(run())
    assert seen[0] == seen[1] == "https://example.com/support?a=1&b=2"
    assert seen[2] is None  # a day on, the feed is read again and has changed
    assert len(host.requests) == 2


def test_two_shows_are_asked_separately() -> None:
    host = FakeHost(_feed(), _feed("<channel></channel>"))
    links = _links(host)
    other = _episode(feed_url="https://example.org/other.xml")

    async def run() -> tuple[str | None, str | None]:
        return await links.for_episode(_episode(), False), await links.for_episode(other, False)

    assert asyncio.run(run()) == ("https://example.com/support?a=1&b=2", None)
    assert [url for url, _ in host.requests] == [FEED_URL, "https://example.org/other.xml"]


def test_a_failed_feed_is_not_asked_again_that_day() -> None:
    clock = Clock()
    host = FakeHost(TimeoutError("slow"), Response(404, {}, b""), _feed())
    links = _links(host, clock)

    async def run() -> list[str | None]:
        seen = [await links.for_episode(_episode(), False)]
        seen.append(await links.for_episode(_episode(), False))  # same day: not asked
        clock.now += FEED_MIN_INTERVAL_S
        seen.append(await links.for_episode(_episode(), False))  # 404
        clock.now += FEED_MIN_INTERVAL_S
        seen.append(await links.for_episode(_episode(), False))
        return seen

    assert asyncio.run(run()) == [None, None, None, "https://example.com/support?a=1&b=2"]
    assert len(host.requests) == 3


def test_feeds_that_left_the_schedule_are_forgotten() -> None:
    clock = Clock()
    host = FakeHost(_feed(), _feed())
    links = _links(host, clock)

    async def run() -> None:
        await links.for_episode(_episode(), False)
        clock.now += FEED_MIN_INTERVAL_S
        await links.for_episode(_episode(feed_url="https://example.org/other.xml"), False)

    asyncio.run(run())
    assert list(links._feeds) == ["https://example.org/other.xml"]


# -- against the saved schedule -----------------------------------------------


def test_every_show_in_the_saved_schedule_would_ask_its_own_feed_once() -> None:
    schedule = parse_schedule(FIXTURE)
    shows = [e for s in schedule.stations for e in s.episodes if not e.is_bulletin]
    feeds = {e.feed_url for e in shows}
    host = FakeHost(*[_feed() for _ in feeds])
    links = _links(host)

    async def run() -> None:
        for episode in shows:
            await links.for_episode(episode, has_support_urls(FIXTURE))

    asyncio.run(run())
    assert sorted(url for url, _ in host.requests) == sorted(feeds)
