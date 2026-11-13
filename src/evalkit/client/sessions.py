import httpx2

from evalkit.client.api import ApiRequestError, AsyncApi, encode
from evalkit.core.agents import AgentDefinition
from evalkit.core.traces.session_import import SessionPullFailed, TraceSourceSessionReader
from evalkit.core.traces.sessions import SessionReader, SessionTooLarge, SessionTrajectory


class HttpSessionReader:
    def __init__(self, api: AsyncApi):
        self._api = api

    async def trajectory(self, agent: str, session_id: str) -> SessionTrajectory:
        path = f"/api/agents/{encode(agent)}/sessions/{encode(session_id)}/trajectory"
        try:
            resp = await self._api.get(path)
        except ApiRequestError as exc:
            if exc.status == 413:
                raise SessionTooLarge(exc.detail) from exc
            raise
        return SessionTrajectory.model_validate(resp.json())


STORE_FAILURES = frozenset({502, 503, 504})


class HttpSessionPuller:
    def __init__(self, api: AsyncApi):
        self._api = api

    async def __call__(self, agent: str, session_id: str) -> None:
        path = f"/api/agents/{encode(agent)}/sessions/import"
        try:
            await self._api.post(path, json={"session_id": session_id})
        except ApiRequestError as exc:
            if exc.status not in STORE_FAILURES:
                raise
            raise SessionPullFailed(exc.detail) from exc
        except httpx2.TimeoutException as exc:
            raise SessionPullFailed(f"pulling session {session_id} timed out: {exc}") from exc


def build_session_reader(api: AsyncApi, definition: AgentDefinition) -> SessionReader:
    reader = HttpSessionReader(api)
    if definition.settings.trace_source is None:
        return reader
    return TraceSourceSessionReader(reader, HttpSessionPuller(api))
