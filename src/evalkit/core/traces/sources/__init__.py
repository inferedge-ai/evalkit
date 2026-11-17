import httpx2

from evalkit.contracts.enums import TraceProvider
from evalkit.core.retry import Retry, RetryTransport
from evalkit.core.traces.sources.base import (
    BadTraceFilter,
    RateLimited,
    TracePage,
    TraceSourceClient,
    UnknownProject,
)
from evalkit.core.traces.sources.langfuse import LangfuseClient, check_langfuse_key
from evalkit.core.traces.sources.phoenix import PhoenixClient

__all__ = [
    "IMPORT_RETRY",
    "BadTraceFilter",
    "RateLimited",
    "TracePage",
    "TraceSourceClient",
    "UnknownProject",
    "check_api_key",
    "import_retry",
    "client_for",
    "newest_first",
]

LANGFUSE_TIMEOUT_SECONDS = 30.0
IMPORT_RETRY = Retry(
    total=2,
    status_forcelist=(429, 500, 502, 503, 504),
    retry_on_exceptions=(httpx2.NetworkError, httpx2.TimeoutException),
    backoff_factor=1.0,
    max_backoff_wait=15.0,
)


def newest_first(provider: TraceProvider) -> bool:
    return provider is TraceProvider.LANGFUSE


def import_retry(provider: TraceProvider) -> Retry | None:
    return IMPORT_RETRY if provider is TraceProvider.LANGFUSE else None


def check_api_key(provider: TraceProvider, api_key: str | None) -> None:
    if provider is TraceProvider.LANGFUSE:
        check_langfuse_key(api_key)


def client_for(
    provider: TraceProvider,
    url: str,
    api_key: str | None,
    page_traces: int,
    transport: httpx2.AsyncBaseTransport | None = None,
    timeout: float = 120.0,
    retry: Retry | None = None,
) -> TraceSourceClient:
    if retry is not None:
        transport = RetryTransport(transport or httpx2.AsyncHTTPTransport(), retry)
    match provider:
        case TraceProvider.PHOENIX:
            return PhoenixClient(url, api_key, page_traces, transport=transport, timeout=timeout)
        case TraceProvider.LANGFUSE:
            return LangfuseClient(
                url,
                api_key,
                page_traces,
                transport=transport,
                timeout=min(timeout, LANGFUSE_TIMEOUT_SECONDS),
            )
