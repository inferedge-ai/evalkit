import asyncio
import ipaddress
import socket

import httpx


class BlockedEndpoint(ValueError):
    pass


def blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not addr.is_global or addr.is_multicast or addr.is_reserved


async def check_endpoint(url: str, *, allow_private: bool) -> None:
    if allow_private:
        return
    host = httpx.URL(url).host
    if not host:
        raise BlockedEndpoint(f"{url} has no host")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedEndpoint(f"{url}: cannot resolve {host}") from exc
    bad = sorted({str(info[4][0]) for info in infos if blocked(ipaddress.ip_address(info[4][0]))})
    if bad:
        raise BlockedEndpoint(
            f"{url} resolves to a private address ({', '.join(bad)}); "
            "the operator can allow it with allow_private_endpoints: true"
        )
