from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from evalkit.credentials import CredentialsConfig, load_dotenv_beside


class MissingEnvError(ValueError):
    def __init__(self, source: Path, key: str, name: str):
        self.source = source
        self.key = key
        self.name = name
        super().__init__(f"{source}: {key} needs env {name} (or env:{name}:<default>)")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ServerConfig(ConfigModel):
    base_url: str
    accent_color: str | None = None


class DbConfig(ConfigModel):
    url: str
    migrate_on_start: bool = False


class BlobConfig(ConfigModel):
    url: str


class CostMapConfig(ConfigModel):
    source: str = "bundled"


class AuthConfig(ConfigModel):
    token_ttl_hours: int = 720


class McpOAuthConfig(ConfigModel):
    issuer: str
    jwks_uri: str
    audience: str | None = None
    algorithm: str = "RS256"
    scope: str = "evalkit:read"


class McpConfig(ConfigModel):
    oauth: McpOAuthConfig | None = None


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


class Config(RunnerConfig):
    server: ServerConfig
    db: DbConfig
    blob: BlobConfig
    auth: AuthConfig = AuthConfig()
    mcp: McpConfig = McpConfig()
    otel: OtelConfig = OtelConfig()
    limits: LimitsConfig = LimitsConfig()
    sentry: SentryConfig | None = None
    encryption_key: str | None = None
    allow_private_endpoints: bool = False

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
