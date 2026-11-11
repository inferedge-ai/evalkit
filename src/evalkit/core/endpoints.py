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


async def check_endpoint(url: str, *, rule: EndpointRule) -> None:
    if rule.allow_private:
        return
    try:
        parsed = httpx2.URL(url)
    except httpx2.InvalidURL as exc:
        raise BlockedEndpoint(f"{url} is not a valid URL: {exc}") from exc
    if get_origin(parsed) in {get_origin(httpx2.URL(t)) for t in rule.trusted}:
        return
    host = parsed.host
    if not host:
        raise BlockedEndpoint(f"{url} has no host")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as exc:
        raise BlockedEndpoint(f"{url}: cannot resolve {host}") from exc
    bad = sorted({str(info[4][0]) for info in infos if blocked(ipaddress.ip_address(info[4][0]))})
    if bad:
        raise BlockedEndpoint(
            f"{url} resolves to a private address ({', '.join(bad)}); "
            "the operator can allow it with allow_private_endpoints: true or trusted_endpoints"
        )
