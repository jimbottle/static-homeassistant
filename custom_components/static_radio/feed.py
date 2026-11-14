"""Asks a show's own host for its RSS feed, carefully.

support.py reads a show's Support link from its feed when the schedule carries
none. The feed's address comes from Static's schedule and its host belongs to
a third party, who can answer with a redirect to anywhere. Home Assistant sits
inside a home network, so this file is the one place a feed is requested, and
it keeps four rules:

- Only web addresses, and only hosts that resolve to public addresses. A host
  that resolves to this machine, the home network, a link-local or a cloud
  metadata address is not asked, on the first request or after any redirect.
- The connection goes to the address that was checked, not to whatever the
  name resolves to a moment later.
- Redirects are followed by hand, a few at most, each one checked the same way.
- Only the start of the feed is read: up to the first episode or 256 KB,
  whichever comes first, inside one time limit for the whole exchange.

It also keeps the one SupportLinks for the life of Home Assistant, so the
once-a-day floor on each feed survives a reload.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Mapping

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration
from yarl import URL

from .api import Fetch, Response
from .const import DOMAIN
from .support import CHANNEL_MAX_BYTES, SupportLinks

FEED_TIMEOUT_S = 20  # for the whole exchange, redirects included
MAX_REDIRECTS = 5
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_CHUNK_BYTES = 16 * 1024
_FIRST_ITEM = b"<item"

# IPv6 ranges that carry an IPv4 address inside them, which could be a private one.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")

_SUPPORT_KEY = f"{DOMAIN}.support"


class FeedRefusedError(Exception):
    """The feed's address is not one this integration will ask."""


def is_public(address: str) -> bool:
    """True for an address on the public internet."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None or ip.teredo is not None or ip in _NAT64:
            return False
    return ip.is_global and not ip.is_multicast


async def _resolve(hass: HomeAssistant, host: str) -> list[str]:
    """Every address a host name resolves to. A literal address resolves to itself."""
    found = await hass.loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in found]


async def public_address(hass: HomeAssistant, host: str) -> str:
    """One checked address for the host. Refuses unless every address is public."""
    try:
        addresses = await _resolve(hass, host)
    except OSError as err:
        raise FeedRefusedError(f"{host} does not resolve") from err
    if not addresses or not all(is_public(address) for address in addresses):
        raise FeedRefusedError(f"{host} is not a public address")
    return addresses[0]


async def _read_start(response: aiohttp.ClientResponse) -> bytes:
    """The feed up to its first episode, or CHANNEL_MAX_BYTES of it."""
    start = b""
    async for chunk in response.content.iter_chunked(_CHUNK_BYTES):
        # Look again from a few bytes back, in case the tag straddles two chunks.
        seen = max(0, len(start) - len(_FIRST_ITEM))
        start += chunk
        if len(start) >= CHANNEL_MAX_BYTES or _FIRST_ITEM in start[seen:]:
            break
    return start[:CHANNEL_MAX_BYTES]


def feed_fetch(hass: HomeAssistant, session: aiohttp.ClientSession) -> Fetch:
    """The one GET support.py asks for, with the rules at the top of this file."""

    async def one_hop(url: URL, headers: Mapping[str, str]) -> Response | URL:
        """Ask one address. A Response to return, or the URL it redirects to."""
        if url.scheme not in ("http", "https") or not url.host or url.user or url.password:
            raise FeedRefusedError("not a plain web address")
        address = await public_address(hass, url.host)
        # Connect to the checked address, and name the host for the server
        # and for the certificate check.
        named = url.host if url.is_default_port() else f"{url.host}:{url.port}"
        sent = {**headers, "Host": named}
        async with session.get(
            url.with_host(address),
            headers=sent,
            allow_redirects=False,
            server_hostname=url.host if url.scheme == "https" else None,
        ) as response:
            location = response.headers.get("Location")
            if response.status in _REDIRECTS and location:
                return url.join(URL(location))
            return Response(response.status, dict(response.headers), await _read_start(response))

    async def fetch(url: str, headers: Mapping[str, str]) -> Response:
        target = URL(url)
        async with asyncio.timeout(FEED_TIMEOUT_S):
            for _ in range(MAX_REDIRECTS + 1):
                answer = await one_hop(target, headers)
                if isinstance(answer, Response):
                    return answer
                target = answer
        raise FeedRefusedError("too many redirects")

    return fetch


async def support_links(hass: HomeAssistant) -> SupportLinks:
    """The Support link lookup, made once and kept while Home Assistant runs."""
    links: SupportLinks | None = hass.data.get(_SUPPORT_KEY)
    if links is None:
        integration = await async_get_integration(hass, DOMAIN)
        links = SupportLinks(
            feed_fetch(hass, async_get_clientsession(hass)), str(integration.version)
        )
        hass.data[_SUPPORT_KEY] = links
    return links
