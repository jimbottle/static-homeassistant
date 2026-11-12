"""feed.py asks a show's host for its feed without being steered into the home network.

Names resolve through a table the test controls, and every request is
answered by the harness's mock, so nothing here reaches a network.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.static_radio.api import request_headers
from custom_components.static_radio.feed import (
    MAX_REDIRECTS,
    FeedRefusedError,
    feed_fetch,
    is_public,
)
from custom_components.static_radio.support import CHANNEL_MAX_BYTES, funding_url

PUBLIC = "93.184.216.34"
NAMES = {
    "feeds.example": [PUBLIC],
    "inside.example": ["192.168.1.10"],
    "both.example": [PUBLIC, "10.0.0.5"],
}
FEED = (
    '<rss><channel><title>A Show</title><podcast:funding url="https://example.org/support">'
    "Support</podcast:funding><item><title>One</title></item></channel></rss>"
)
HEADERS = request_headers("0.0.0")


@pytest.fixture(autouse=True)
def _names() -> Iterator[None]:
    async def resolve(_hass: HomeAssistant, host: str) -> list[str]:
        if host in NAMES:
            return NAMES[host]
        return [host]  # a literal address resolves to itself

    with patch("custom_components.static_radio.feed._resolve", resolve):
        yield


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("::1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),  # noqa: S104 - an address under test, nothing binds to it
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:192.168.1.1", False),  # a private address in IPv6 clothing
        ("64:ff9b::c0a8:101", False),
        ("2002:c0a8:101::1", False),
        ("224.0.0.1", False),
        ("not an address", False),
    ],
)
def test_only_addresses_on_the_public_internet_pass(address: str, public: bool) -> None:
    assert is_public(address) is public


async def test_a_public_feed_is_asked_at_the_checked_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"https://{PUBLIC}/show.rss", text=FEED)
    fetch = feed_fetch(hass, async_get_clientsession(hass))

    response = await fetch("https://feeds.example/show.rss", HEADERS)

    assert response.status == 200
    assert funding_url(response.body.decode()) == "https://example.org/support"
    assert aioclient_mock.call_count == 1
    sent = aioclient_mock.mock_calls[0][3]
    assert sent["Host"] == "feeds.example"
    assert sent["User-Agent"] == "HomeAssistant-Static/0.0.0"


@pytest.mark.parametrize(
    "url",
    [
        "https://inside.example/show.rss",
        "https://both.example/show.rss",  # one private address is enough to refuse
        "http://127.0.0.1:8123/api/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://feeds.example/show.rss",
        "https://user:secret@feeds.example/show.rss",
    ],
)
async def test_an_address_inside_the_house_is_never_asked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, url: str
) -> None:
    fetch = feed_fetch(hass, async_get_clientsession(hass))
    with pytest.raises(FeedRefusedError):
        await fetch(url, HEADERS)
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "https://inside.example/show.rss",
    ],
)
async def test_a_redirect_into_the_house_is_not_followed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, location: str
) -> None:
    aioclient_mock.get(f"https://{PUBLIC}/show.rss", status=302, headers={"Location": location})
    fetch = feed_fetch(hass, async_get_clientsession(hass))

    with pytest.raises(FeedRefusedError):
        await fetch("https://feeds.example/show.rss", HEADERS)

    assert aioclient_mock.call_count == 1  # the redirect's target was never requested


async def test_a_redirect_to_a_public_host_is_followed_with_the_same_headers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"https://{PUBLIC}/old.rss", status=301, headers={"Location": "/new.rss"})
    aioclient_mock.get(f"https://{PUBLIC}/new.rss", text=FEED)
    fetch = feed_fetch(hass, async_get_clientsession(hass))

    response = await fetch("https://feeds.example/old.rss", HEADERS)

    assert response.status == 200
    assert aioclient_mock.call_count == 2
    assert aioclient_mock.mock_calls[1][3]["User-Agent"] == "HomeAssistant-Static/0.0.0"


async def test_a_redirect_loop_ends(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"https://{PUBLIC}/loop", status=302, headers={"Location": "/loop"})
    fetch = feed_fetch(hass, async_get_clientsession(hass))

    with pytest.raises(FeedRefusedError):
        await fetch("https://feeds.example/loop", HEADERS)

    assert aioclient_mock.call_count == MAX_REDIRECTS + 1


async def test_reading_stops_at_the_first_episode(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    episodes = "<item><title>x</title></item>" * 200_000  # about 6 MB
    aioclient_mock.get(f"https://{PUBLIC}/big.rss", text=FEED.replace("<item>", episodes, 1))
    fetch = feed_fetch(hass, async_get_clientsession(hass))

    response = await fetch("https://feeds.example/big.rss", HEADERS)

    assert len(response.body) <= 16 * 1024
    assert funding_url(response.body.decode()) == "https://example.org/support"


async def test_reading_stops_at_the_size_limit_when_no_episode_comes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"https://{PUBLIC}/junk", content=b"a" * (CHANNEL_MAX_BYTES * 4))
    fetch = feed_fetch(hass, async_get_clientsession(hass))

    response = await fetch("https://feeds.example/junk", HEADERS)

    assert len(response.body) == CHANNEL_MAX_BYTES
