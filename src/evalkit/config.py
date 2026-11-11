from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx2
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evalkit.credentials import CredentialsConfig, load_dotenv_beside


class MissingEnvError(ValueError):
    def __init__(self, source: Path, key: str, name: str):
        self.source = source
        self.key = key
        self.name = name
        super().__init__(f"{source}: {key} needs env {name} (or env:{name}:<default>)")


DEFAULT_PORT = 8020


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ServerConfig(ConfigModel):
    base_url: str
    accent_color: str | None = None

    @field_validator("base_url")
    @classmethod
    def _port_is_a_number(cls, value: str) -> str:
        _ = urlsplit(value).port
        return value

    @property
    def port(self) -> int:
        return urlsplit(self.base_url).port or DEFAULT_PORT


class DbConfig(ConfigModel):
    url: str
    migrate_on_start: bool = False
    pool_size: int = Field(20, ge=1)
    max_overflow: int = Field(10, ge=0)
    pool_timeout_seconds: float = Field(5, gt=0)


class BlobConfig(ConfigModel):
    url: str


class ServerRunnerConfig(ConfigModel):
    evaluation_concurrency: int = Field(default=16, ge=1)


class CostMapConfig(ConfigModel):
    source: str = "bundled"


class AdminClaimConfig(ConfigModel):
    name: str
    value: str


class OidcConfig(ConfigModel):
    issuer: str
    client_id: str
    audience: str
    name: str
    scopes: list[str] = ["openid", "profile", "email", "offline_access"]
    required_scopes: list[str] = []
    authorize_params: dict[str, str] = {}
    algorithms: list[str] = ["RS256"]
    admin_claim: AdminClaimConfig | None = None

    @field_validator("scopes")
    @classmethod
    def _scopes_include_openid(cls, value: list[str]) -> list[str]:
        if "openid" not in value:
            raise ValueError("auth.oidc.scopes must include openid")
        return value

    @model_validator(mode="after")
    def _scopes_include_required_scopes(self) -> OidcConfig:
        missing = [scope for scope in self.required_scopes if scope not in self.scopes]
        if missing:
            raise ValueError(
                f"auth.oidc.required_scopes lists {', '.join(missing)}, "
                "which auth.oidc.scopes does not request; add it to auth.oidc.scopes"
            )
        return self

    @field_validator("issuer")
    @classmethod
    def _issuer_has_no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class AuthConfig(ConfigModel):
    token_ttl_hours: int = 720
    password_login: Literal["admin", "all", "none"] = "admin"
    oidc: OidcConfig | None = None

    @model_validator(mode="after")
    def _require_admin_claim(self) -> AuthConfig:
        if self.password_login == "none" and (self.oidc is None or self.oidc.admin_claim is None):
            raise ValueError(
                "password_login: none needs auth.oidc with admin_claim, so the identity "
                "provider says who is an admin"
            )
        return self

    def resolve_password_login(self) -> Literal["admin", "all", "none"]:
        return "all" if self.oidc is None else self.password_login


class OtelConfig(ConfigModel):
    settle_seconds: int = 10
    max_batch_bytes: int = 16 * 1024 * 1024
    max_session_bytes: int = 384 * 1024 * 1024
    import_page_traces: int = 25


class SentryConfig(ConfigModel):
    dsn: str
    environment: str | None = None
    traces_sample_rate: float = 0.0
    max_request_body_size: Literal["never", "small", "medium", "always"] = "never"


MB = 1024 * 1024


class LimitsConfig(ConfigModel):
    fixture_upload_bytes: int = 500 * MB
    experiment_blob_bytes: int = 500 * MB
    case_upload_bytes: int = 50 * MB
    request_bytes: int = 10 * MB


class RunnerConfig(ConfigModel):
    cost_map: CostMapConfig = CostMapConfig()


# TODO: remove in 0.5.0, two minor versions after 0.3.0 moved credentials and
# judge_default to the database; _shell and _moved_to_db_commands only serve that check.
def _shell(value: object) -> str:
    text = str(value)
    return f"${text.removeprefix('env:')}" if text.startswith("env:") else text


def _moved_to_db_commands(data: dict) -> list[str]:
    credentials = data.get("credentials") or {}
    judge = data.get("judge_default") or {}
    commands = [
        f"evalkit llm-providers create <name> --base-url {_shell(url)} "
        f"--api-key-env {str(ref).removeprefix('env:')}"
        for url, ref in (credentials.get("judges") or {}).items()
    ]
    if judge:
        commands.append(
            f"evalkit judge-default set --provider <name> "
            f"--model {_shell(judge.get('model', '<model>'))}"
        )
    commands += [
        f"evalkit agents edit {ref} --api-key-env {str(env).removeprefix('env:')}"
        for ref, env in (credentials.get("agents") or {}).items()
    ]
    commands += [
        f"evalkit evaluators edit {ref} --api-key-env {str(env).removeprefix('env:')}"
        for ref, env in (credentials.get("evaluators") or {}).items()
    ]
    return commands


def check_trusted_endpoints(urls: list[str]) -> list[str]:
    for i, url in enumerate(urls):
        try:
            parsed = httpx2.URL(url)
        except httpx2.InvalidURL as exc:
            raise ValueError(f"trusted_endpoints[{i}] is not a valid URL") from exc
        if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo:
            raise ValueError(
                f"trusted_endpoints[{i}] must be an http or https URL with a host and no user"
            )
    return urls


class Config(RunnerConfig):
    server: ServerConfig
    db: DbConfig
    blob: BlobConfig
    auth: AuthConfig = AuthConfig()
    otel: OtelConfig = OtelConfig()
    limits: LimitsConfig = LimitsConfig()
    runner: ServerRunnerConfig = ServerRunnerConfig()
    sentry: SentryConfig | None = None
    encryption_key: str | None = None
    allow_private_endpoints: bool = False
    trusted_endpoints: list[str] = []

    @field_validator("trusted_endpoints")
    @classmethod
    def _check_trusted_endpoints(cls, urls: list[str]) -> list[str]:
        return check_trusted_endpoints(urls)

    # TODO: remove in 0.5.0, two minor versions after 0.3.0 moved credentials and
    # judge_default to the database.
    @model_validator(mode="before")
    @classmethod
    def _keys_moved_to_db(cls, data: object) -> object:
        if isinstance(data, dict) and ("credentials" in data or "judge_default" in data):
            commands = "\n".join(f"  {c}" for c in _moved_to_db_commands(data))
            raise ValueError(
                "credentials and judge_default moved to the database. Remove them from the "
                "config, start the server, then run (as an admin, with the keys in your env):\n"
                f"{commands}"
            )
        return data

    # TODO: remove in 0.6.0, two minor versions after 0.4.0 moved mcp.oauth to auth.oidc.
    @model_validator(mode="before")
    @classmethod
    def _mcp_oauth_moved(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("mcp"), dict) and "oauth" in data["mcp"]:
            raise ValueError(
                "mcp.oauth moved to auth.oidc: set auth.oidc.issuer, client_id, audience and "
                "name; /mcp verifies tokens with the same settings as /api"
            )
        return data


LOCAL_CONFIG_FILENAME = "evalkit.yaml"


class LocalConfig(RunnerConfig):
    path: Path
    credentials: CredentialsConfig = CredentialsConfig()
    cases: Path | None = None
    fixtures: Path | None = None

    @classmethod
    def find(cls, start: Path) -> LocalConfig | None:
        start = start.resolve()
        for directory in (start, *start.parents):
            candidate = directory / LOCAL_CONFIG_FILENAME
            if candidate.is_file():
                return load_local_config(candidate)
        return None

    def environ(self) -> dict[str, str]:
        return load_dotenv_beside(self.path)

    def resolve_env(self, *, scoring: bool = True) -> LocalConfig:
        data = self.model_dump()
        if scoring:
            environ = self.environ()
            data["cost_map"] = resolve_env_refs(data["cost_map"], self.path, "cost_map", environ)
        return LocalConfig.model_validate(data)


UNRESOLVED_KEYS = frozenset({"credentials"})


def resolve_env_refs(obj: object, source: Path, key: str, environ: Mapping[str, str]) -> object:
    if isinstance(obj, dict):
        return {
            k: v
            if k in UNRESOLVED_KEYS and not key
            else resolve_env_refs(v, source, _join(key, k), environ)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [
            resolve_env_refs(v, source, f"{key}[{_item_label(v, i)}]", environ)
            for i, v in enumerate(obj)
        ]
    if isinstance(obj, str) and obj.startswith("env:"):
        name, has_default, default = obj[4:].partition(":")
        if has_default:
            return environ.get(name, default)
        if name not in environ:
            raise MissingEnvError(source, key, name)
        return environ[name]
    return obj


def _join(key: str, child: object) -> str:
    return f"{key}.{child}" if key else str(child)


def _item_label(item: object, index: int) -> object:
    if isinstance(item, dict) and isinstance(item.get("name"), str):
        return item["name"]
    return index


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())
    data = resolve_env_refs(raw, path, "", os.environ)
    return Config.model_validate(data)


def load_local_config(path: Path) -> LocalConfig:
    data = yaml.safe_load(path.read_text()) or {}
    assert isinstance(data, dict)
    base = path.resolve().parent
    cfg = LocalConfig.model_validate({**data, "path": path.resolve()})
    return cfg.model_copy(
        update={
            "cases": (base / cfg.cases).resolve() if cfg.cases is not None else None,
            "fixtures": (base / cfg.fixtures).resolve() if cfg.fixtures is not None else None,
        }
    )
