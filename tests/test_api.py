"""api.py keeps Static's request rules: which URLs, how often, which headers.

No network: each test hands the client a fake GET and a clock it controls.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from api import (
    COUNTS_MIN_INTERVAL_S,
    COUNTS_URL,
    SCHEDULE_MIN_INTERVAL_S,
    SCHEDULE_URL,
    CountsClient,
    Response,
    ScheduleClient,
    request_headers,
    user_agent,
)

SCHEDULE = {"schema_version": 8, "categories": {}}


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


def _ok(body: Any, etag: str | None = None) -> Response:
    headers = {"etag": etag} if etag else {}
    return Response(200, headers, json.dumps(body).encode())


def test_user_agent_names_the_app_and_version() -> None:
    assert user_agent("0.1.0") == "HomeAssistant-Static/0.1.0"
    assert request_headers("0.1.0") == {
        "User-Agent": "HomeAssistant-Static/0.1.0",
        "Accept-Language": "en",
    }


def test_schedule_is_fetched_once_then_served_from_the_copy_for_15_minutes() -> None:
    clock, host = Clock(), FakeHost(_ok(SCHEDULE, '"abc"'))
    client = ScheduleClient(host.fetch, "0.1.0", clock)

    async def run() -> None:
        assert await client.get() == SCHEDULE
        clock.now += SCHEDULE_MIN_INTERVAL_S - 1
        assert await client.get() == SCHEDULE

    asyncio.run(run())
    assert len(host.requests) == 1
    url, headers = host.requests[0]
    assert url == SCHEDULE_URL
    assert headers["User-Agent"] == "HomeAssistant-Static/0.1.0"
    assert headers["Accept-Language"] == "en"
    assert "If-None-Match" not in headers


def test_the_next_fetch_sends_the_etag_and_a_304_keeps_the_copy() -> None:
    clock = Clock()
    host = FakeHost(_ok(SCHEDULE, '"abc"'), Response(304, {}, b""))
    client = ScheduleClient(host.fetch, "0.1.0", clock)

    async def run() -> None:
        await client.get()
        clock.now += SCHEDULE_MIN_INTERVAL_S
        assert await client.get() == SCHEDULE

    asyncio.run(run())
    assert len(host.requests) == 2
    assert host.requests[1][1]["If-None-Match"] == '"abc"'


def test_a_new_file_replaces_the_copy_and_its_etag() -> None:
    clock = Clock()
    newer = {"schema_version": 8, "categories": {"news_01": {}}}
    host = FakeHost(_ok(SCHEDULE, '"abc"'), _ok(newer, '"def"'), Response(304, {}, b""))
    client = ScheduleClient(host.fetch, "0.1.0", clock)

    async def run() -> None:
        await client.get()
        clock.now += SCHEDULE_MIN_INTERVAL_S
        assert await client.get() == newer
        clock.now += SCHEDULE_MIN_INTERVAL_S
        await client.get()

    asyncio.run(run())
    assert host.requests[2][1]["If-None-Match"] == '"def"'


def test_a_failed_fetch_keeps_the_copy_and_still_waits_15_minutes() -> None:
    clock = Clock()
    host = FakeHost(
        _ok(SCHEDULE, '"abc"'),
        OSError("no route"),
        Response(503, {}, b""),
        Response(200, {}, b"not json"),
        Response(200, {}, b"[]"),
    )
    client = ScheduleClient(host.fetch, "0.1.0", clock)

    async def run() -> None:
        await client.get()
        for _ in range(4):
            clock.now += SCHEDULE_MIN_INTERVAL_S
            assert await client.get() == SCHEDULE
            clock.now += 1
            assert await client.get() == SCHEDULE  # no retry inside the floor

    asyncio.run(run())
    assert len(host.requests) == 5


def test_no_copy_yet_and_a_failure_gives_none() -> None:
    client = ScheduleClient(FakeHost(OSError("down")).fetch, "0.1.0", Clock())
    assert asyncio.run(client.get()) is None
    assert client.raw is None


def test_at_most_four_schedule_requests_in_an_hour() -> None:
    clock = Clock()
    host = FakeHost(_ok(SCHEDULE, '"abc"'), *[Response(304, {}, b"") for _ in range(10)])
    client = ScheduleClient(host.fetch, "0.1.0", clock)

    async def run() -> None:
        for _ in range(3600):  # asked every second for an hour
            await client.get()
            clock.now += 1

    asyncio.run(run())
    assert len(host.requests) == 4
    assert {url for url, _ in host.requests} == {SCHEDULE_URL}


def test_two_callers_at_once_make_one_request() -> None:
    host = FakeHost(_ok(SCHEDULE, '"abc"'))
    client = ScheduleClient(host.fetch, "0.1.0", Clock())

    async def run() -> None:
        await asyncio.gather(client.get(), client.get(), client.get())

    asyncio.run(run())
    assert len(host.requests) == 1


def test_counts_are_fetched_at_most_once_a_minute() -> None:
    clock = Clock()
    host = FakeHost(
        _ok({"counts": {"news_01": 3, "work_01": None}}),
        _ok({"counts": {"news_01": 4, "work_01": 1}}),
        *[_ok({"counts": {}}) for _ in range(70)],
    )
    client = CountsClient(host.fetch, "0.1.0", clock)

    async def run() -> None:
        assert await client.get() == {"news_01": 3, "work_01": None}
        clock.now += COUNTS_MIN_INTERVAL_S - 1
        assert await client.get() == {"news_01": 3, "work_01": None}
        clock.now += 1
        assert await client.get() == {"news_01": 4, "work_01": 1}
        for _ in range(3600 - 61):  # asked every second for the rest of the hour
            clock.now += 1
            await client.get()

    asyncio.run(run())
    assert len(host.requests) <= 60
    assert {url for url, _ in host.requests} == {COUNTS_URL}
    assert all(h["User-Agent"] == "HomeAssistant-Static/0.1.0" for _, h in host.requests)


def test_counts_never_fail_the_caller() -> None:
    clock = Clock()
    host = FakeHost(
        _ok({"counts": {"news_01": 3}}),
        OSError("down"),
        Response(500, {}, b""),
        Response(200, {}, b"<html>"),
    )
    client = CountsClient(host.fetch, "0.1.0", clock)

    async def run() -> None:
        await client.get()
        for _ in range(3):
            clock.now += COUNTS_MIN_INTERVAL_S
            assert await client.get() == {"news_01": 3}

    asyncio.run(run())


def test_header_lookup_ignores_case() -> None:
    assert Response(200, {"ETag": '"x"'}, b"").header("etag") == '"x"'
    assert Response(200, {}, b"").header("etag") is None
