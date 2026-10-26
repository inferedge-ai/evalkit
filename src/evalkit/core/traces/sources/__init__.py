import httpx

from evalkit.contracts.enums import TraceProvider
from evalkit.core.traces.sources.base import TracePage, TraceSourceClient
from evalkit.core.traces.sources.phoenix import PhoenixClient

__all__ = ["TracePage", "TraceSourceClient", "client_for"]


def client_for(
    provider: TraceProvider,
    url: str,
    api_key: str | None,
    page_traces: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TraceSourceClient:
    match provider:
        case TraceProvider.PHOENIX:
            return PhoenixClient(url, api_key, page_traces, transport=transport)
