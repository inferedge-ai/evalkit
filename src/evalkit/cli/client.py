import httpx2
import typer

from evalkit import version
from evalkit.client import oidc
from evalkit.client.api import ApiRequestError, SyncApi


class ApiError(typer.Exit):
    def __init__(self, message: str):
        typer.secho(message, fg="red", err=True)
        super().__init__(code=1)


class ApiClient(SyncApi):
    def __init__(
        self,
        server_url: str,
        auth: httpx2.Auth | None = None,
        *,
        transport: httpx2.BaseTransport | None = None,
        check_version: bool = True,
    ):
        super().__init__(server_url, auth, transport=transport)
        self._server_url = server_url.rstrip("/")
        self._version_checked = not check_version

    def _check_version(self, resp: httpx2.Response) -> None:
        if self._version_checked:
            return
        self._version_checked = True
        remote = resp.headers.get("X-Evalkit-Version")
        if remote is None:
            return
        local = version.current()
        match version.compare(local, remote):
            case version.VersionComparison.SAME:
                return
            case version.VersionComparison.INCOMPATIBLE:
                raise ApiError(
                    f"evalkit {local} cannot talk to server {remote}: "
                    f"run `evalkit update --server {self._server_url}`"
                )
            case version.VersionComparison.DIFFERENT:
                typer.secho(
                    f"evalkit {local} differs from server {remote}; "
                    f"run `evalkit update --server {self._server_url}`",
                    fg="yellow",
                    err=True,
                )

    def request(
        self, method: str, path: str, *, retry: bool | None = None, **kwargs
    ) -> httpx2.Response:
        try:
            if not self._version_checked and method not in ("GET", "HEAD"):
                self._check_version(self._client.get("/api/health"))
            return super().request(method, path, retry=retry, **kwargs)
        except (oidc.LoginExpired, oidc.ClientCredentialsRefused) as exc:
            raise ApiError(str(exc)) from exc

    def _handle(self, resp: httpx2.Response) -> httpx2.Response:
        self._check_version(resp)
        if resp.status_code >= 400:
            raise ApiError(str(ApiRequestError.from_response(resp)))
        return resp

    def head(self, path: str, **kwargs) -> httpx2.Response:
        resp = super().head(path, **kwargs)
        self._check_version(resp)
        return resp
