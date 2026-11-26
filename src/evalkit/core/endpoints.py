import asyncio
import ipaddress
import socket
from dataclasses import dataclass

import httpx2


class BlockedEndpoint(ValueError):
    pass


@dataclass(frozen=True)
class EndpointRule:
    allow_private: bool
    trusted: tuple[str, ...] = ()


def blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not addr.is_global or addr.is_multicast or addr.is_reserved


def get_origin(url: httpx2.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port


def parse_endpoint(url: str, *, owner: str | None = None) -> httpx2.URL:
    prefix = f"{owner}: " if owner else ""
    try:
        parsed = httpx2.URL(url)
    except httpx2.InvalidURL as exc:
        raise BlockedEndpoint(f"{prefix}{url} is not a valid URL: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise BlockedEndpoint(f"{prefix}{url} is not a URL that starts with http:// or https://")
    if not parsed.host:
        raise BlockedEndpoint(f"{prefix}{url} has no host; a URL looks like https://host/path")
    if parsed.port is not None and not 0 < parsed.port < 65536:
        raise BlockedEndpoint(f"{prefix}{url} has port {parsed.port}; a port is 1 to 65535")
    return parsed


async def check_endpoint(url: str, *, rule: EndpointRule, owner: str | None = None) -> None:
    parsed = parse_endpoint(url, owner=owner)
    prefix = f"{owner}: " if owner else ""
    if rule.allow_private:
        return
    if get_origin(parsed) in {get_origin(httpx2.URL(t)) for t in rule.trusted}:
        return
    host = parsed.host
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as exc:
        raise BlockedEndpoint(f"{prefix}{url}: cannot resolve {host}") from exc
    bad = sorted({str(info[4][0]) for info in infos if blocked(ipaddress.ip_address(info[4][0]))})
    if bad:
        raise BlockedEndpoint(
            f"{prefix}{url} resolves to a private address ({', '.join(bad)}); "
            "the operator can allow it with allow_private_endpoints: true or trusted_endpoints"
        )
