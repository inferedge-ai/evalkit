import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import anyio
import httpx2


@dataclass(frozen=True)
class Retry:
    total: int = 0
    allowed_methods: tuple[str, ...] = ("GET", "HEAD")
    status_forcelist: tuple[int, ...] = ()
    retry_on_exceptions: tuple[type[Exception], ...] = ()
    backoff_factor: float = 0.0
    max_backoff_wait: float = 120.0

    def is_retryable_method(self, method: str) -> bool:
        return method.upper() in self.allowed_methods

    def backoff(self, attempt: int) -> float:
        return min(self.max_backoff_wait, self.backoff_factor * 2**attempt)

    def wait(self, response: httpx2.Response | None, attempt: int) -> float:
        if response is not None:
            retry_after = parse_retry_after(response.headers.get("Retry-After", ""))
            if retry_after is not None and retry_after > 0:
                return min(retry_after, self.max_backoff_wait)
        return self.backoff(attempt)


def parse_retry_after(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())


def is_replayable(request: httpx2.Request) -> bool:
    return isinstance(request.stream, httpx2.SyncByteStream) and isinstance(
        request.stream, httpx2.AsyncByteStream
    )


class RetryTransport(httpx2.BaseTransport, httpx2.AsyncBaseTransport):
    def __init__(
        self,
        transport: httpx2.BaseTransport | httpx2.AsyncBaseTransport,
        retry: Retry,
    ):
        self.transport = transport
        self.retry = retry

    def _policy(self, request: httpx2.Request) -> Retry:
        return request.extensions.get("retry", self.retry)

    def _is_last(self, policy: Retry, request: httpx2.Request, attempt: int) -> bool:
        return (
            attempt >= policy.total
            or not policy.is_retryable_method(request.method)
            or not is_replayable(request)
        )

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        assert isinstance(self.transport, httpx2.BaseTransport)
        policy = self._policy(request)
        attempt = 0
        while True:
            last = self._is_last(policy, request, attempt)
            try:
                response = self.transport.handle_request(request)
            except policy.retry_on_exceptions:
                if last:
                    raise
                time.sleep(policy.wait(None, attempt))
                attempt += 1
                continue
            if last or response.status_code not in policy.status_forcelist:
                return response
            response.close()
            time.sleep(policy.wait(response, attempt))
            attempt += 1

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        assert isinstance(self.transport, httpx2.AsyncBaseTransport)
        policy = self._policy(request)
        attempt = 0
        while True:
            last = self._is_last(policy, request, attempt)
            try:
                response = await self.transport.handle_async_request(request)
            except policy.retry_on_exceptions:
                if last:
                    raise
                await anyio.sleep(policy.wait(None, attempt))
                attempt += 1
                continue
            if last or response.status_code not in policy.status_forcelist:
                return response
            await response.aclose()
            await anyio.sleep(policy.wait(response, attempt))
            attempt += 1

    def close(self) -> None:
        assert isinstance(self.transport, httpx2.BaseTransport)
        self.transport.close()

    async def aclose(self) -> None:
        assert isinstance(self.transport, httpx2.AsyncBaseTransport)
        await self.transport.aclose()
