from evalkit.client.api import ApiRequestError, AsyncApi, encode
from evalkit.core.traces.sessions import SessionTooLarge, SessionTrajectory


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
