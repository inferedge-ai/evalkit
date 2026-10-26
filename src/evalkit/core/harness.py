import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import IO
from urllib.parse import quote

import httpx

from evalkit.contracts.atif import Trajectory, validate_trajectory
from evalkit.contracts.harness import (
    Capabilities,
    FileList,
    FileRef,
    RunList,
    RunListParams,
    RunMetadata,
    RunStatus,
)
from evalkit.core.agents import AgentDefinition
from evalkit.core.paths import require_safe_path, require_safe_run_id

LOGGER = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
GET_ATTEMPTS = 3
STALE_CONNECTION_ERRORS = (httpx.RemoteProtocolError, httpx.ReadError)
# Shorter than the 5s idle keep-alive of uvicorn and Node, so evalkit drops an
# idle connection before the harness closes it under an in-flight poll.
KEEPALIVE_EXPIRY_SECONDS = 2


@dataclass
class HarnessFile:
    size: int | None
    chunks: AsyncIterator[bytes]


async def _response_chunks(resp: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk
    finally:
        await resp.aclose()


class RunTimeout(Exception):
    def __init__(self, run_id: str, last_status: RunStatus | None):
        self.run_id = run_id
        self.last_status = last_status
        super().__init__(f"run {run_id} did not reach a terminal status in time")


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    message = f"harness returned {resp.status_code} for {resp.request.method} {resp.request.url}"
    body = resp.text.strip()
    if body:
        message += f": {body[:1000]}"
    raise httpx.HTTPStatusError(message, request=resp.request, response=resp)


class HarnessClient:
    def __init__(
        self,
        agent: AgentDefinition,
        api_key: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        assert agent.harness_url is not None
        self.agent = agent
        self._client = httpx.AsyncClient(
            base_url=agent.harness_url.rstrip("/"),
            headers={"x-api-key": api_key} if api_key else {},
            timeout=60,
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS),
            transport=transport,
        )
        self._capabilities: Capabilities | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, *, stream: bool = False, **kwargs) -> httpx.Response:
        for attempt in range(1, GET_ATTEMPTS + 1):
            try:
                request = self._client.build_request("GET", url, **kwargs)
                return await self._client.send(request, stream=stream)
            except STALE_CONNECTION_ERRORS as exc:
                if attempt == GET_ATTEMPTS:
                    raise
                LOGGER.warning(
                    "harness GET %s disconnected (attempt %s/%s), retrying: %r",
                    url,
                    attempt,
                    GET_ATTEMPTS,
                    exc,
                )
        raise AssertionError("unreachable")

    async def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            resp = await self._get("/capabilities")
            _raise_for_status(resp)
            self._capabilities = Capabilities.model_validate(resp.json())
        return self._capabilities

    async def start_run(
        self,
        input: dict,
        files: Sequence[tuple[str, bytes | IO[bytes]]] | None = None,
        file_refs: list[FileRef] | None = None,
        model: str | None = None,
        metadata: RunMetadata | None = None,
    ) -> str:
        payload: dict = {"input": input}
        if model is not None:
            payload["model"] = model
        if metadata is not None:
            payload["metadata"] = metadata.model_dump(exclude_none=True)
        if file_refs:
            payload["files"] = [ref.model_dump(exclude_none=True) for ref in file_refs]
        if files:
            parts: list[
                tuple[str, tuple[str | None, str | bytes, str] | tuple[str, bytes | IO[bytes]]]
            ] = [("payload", (None, json.dumps(payload), "application/json"))]
            parts += [("files", (name, data)) for name, data in files]
            resp = await self._client.post("/runs", files=parts)
        else:
            resp = await self._client.post("/runs", json=payload)
        _raise_for_status(resp)
        return resp.json()["run_id"]

    async def status(self, run_id: str) -> RunStatus:
        resp = await self._get(f"/runs/{_run_segment(run_id)}")
        _raise_for_status(resp)
        return RunStatus.model_validate(resp.json())

    async def poll_until_terminal(self, run_id: str) -> RunStatus:
        deadline = time.monotonic() + self.agent.settings.run_timeout_seconds
        last: RunStatus | None = None
        while time.monotonic() < deadline:
            last = await self.status(run_id)
            if last.status in TERMINAL_STATUSES:
                return last
            await asyncio.sleep(self.agent.settings.poll_interval_seconds)
        caps = await self.capabilities()
        if caps.accepts.stop:
            await self.stop(run_id)
            last = await self._poll_after_stop(run_id, last)
        else:
            LOGGER.warning(
                "run %s timed out and harness does not accept stop — resources may leak",
                run_id,
            )
        raise RunTimeout(run_id, last)

    async def _poll_after_stop(self, run_id: str, last: RunStatus | None) -> RunStatus | None:
        deadline = time.monotonic() + self.agent.settings.stop_grace_seconds
        while time.monotonic() < deadline:
            last = await self.status(run_id)
            if last.status in TERMINAL_STATUSES:
                return last
            await asyncio.sleep(self.agent.settings.poll_interval_seconds)
        LOGGER.warning(
            "run %s not terminal %ss after stop; fetching its trajectory anyway",
            run_id,
            self.agent.settings.stop_grace_seconds,
        )
        return last

    async def stop(self, run_id: str) -> None:
        resp = await self._client.post(f"/runs/{_run_segment(run_id)}/stop")
        _raise_for_status(resp)

    async def trajectory(self, run_id: str) -> Trajectory:
        resp = await self._get(f"/runs/{_run_segment(run_id)}/trajectory")
        _raise_for_status(resp)
        return validate_trajectory(resp.json())

    async def list_runs(self, params: RunListParams | None = None) -> RunList:
        resp = await self._get("/runs", params=(params or RunListParams()).as_query())
        _raise_for_status(resp)
        return RunList.model_validate(resp.json())

    async def list_files(self, run_id: str) -> FileList:
        resp = await self._get(f"/runs/{_run_segment(run_id)}/files")
        _raise_for_status(resp)
        return FileList.model_validate(resp.json())

    async def _file_response(self, run_id: str, path: str) -> httpx.Response:
        return await self._get(f"/runs/{_run_segment(run_id)}/files/{_file_path(path)}")

    async def find_file(self, run_id: str, path: str) -> bytes | None:
        resp = await self._file_response(run_id, path)
        if resp.status_code == 404:
            return None
        _raise_for_status(resp)
        return resp.content

    async def get_file(self, run_id: str, path: str) -> bytes:
        resp = await self._file_response(run_id, path)
        _raise_for_status(resp)
        return resp.content

    async def stream_file(self, run_id: str, path: str) -> HarnessFile:
        url = f"/runs/{_run_segment(run_id)}/files/{_file_path(path)}"
        resp = await self._get(url, stream=True)
        if not resp.is_success:
            await resp.aread()
            await resp.aclose()
            _raise_for_status(resp)
        length = resp.headers.get("content-length")
        size = int(length) if length and "content-encoding" not in resp.headers else None
        return HarnessFile(size=size, chunks=_response_chunks(resp))


def _run_segment(run_id: str) -> str:
    return quote(require_safe_run_id(run_id), safe="")


def _file_path(path: str) -> str:
    return "/".join(quote(segment, safe="") for segment in require_safe_path(path).split("/"))
