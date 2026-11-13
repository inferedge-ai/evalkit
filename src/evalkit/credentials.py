import re
from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

Kind = Literal["agent", "evaluator"]


class MissingCredential(ValueError):
    pass


ENV_REF = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")


class CredentialsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    agents: dict[str, str] = {}
    evaluators: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _judge_keys_removed(cls, data: object) -> object:
        if isinstance(data, dict) and ({"judges", "llm_providers"} & data.keys()):
            raise ValueError(
                "credentials.llm_providers was removed: pass your own judge's key with "
                "evalkit run --judge-api-key-env NAME or Judge(base_url=..., api_key=...)"
            )
        return data

    @field_validator("agents", "evaluators")
    @classmethod
    def _env_refs_only(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [k for k, v in value.items() if not ENV_REF.match(v)]
        if bad:
            raise ValueError(f"credentials must be env:NAME references (no defaults): {bad}")
        return value


class KeySource(Protocol):
    def key_for(self, kind: Kind, ref: str, url: str | None = None) -> str | None: ...

    def provider_judge(self, name: str) -> tuple[str, str | None]: ...


class CredentialRegistry:
    def __init__(
        self, cfg: CredentialsConfig, environ: Mapping[str, str], *, home: str | None = None
    ):
        self._cfg = cfg
        self._env = environ
        self._home = home

    def _table(self, kind: Kind) -> dict[str, str]:
        return {"agent": self._cfg.agents, "evaluator": self._cfg.evaluators}[kind]

    def _entry(self, kind: Kind, ref: str) -> str | None:
        table = self._table(kind)
        if ref in table:
            return table[ref]
        name, _, workspace = ref.partition("@")
        if self._home is not None and workspace == self._home:
            return table.get(name)
        return None

    def key_for(self, kind: Kind, ref: str, url: str | None = None) -> str | None:
        entry = self._entry(kind, ref)
        if entry is None:
            return None
        name = entry.removeprefix("env:")
        if name not in self._env:
            raise MissingCredential(f"credentials.{kind}s[{ref!r}] needs env var {name}")
        return self._env[name]

    def provider_judge(self, name: str) -> tuple[str, str | None]:
        raise ProviderJudgeIsServerSide(name)


class ProviderJudgeIsServerSide(LookupError):
    def __init__(self, name: str):
        super().__init__(
            f"judge provider {name} is scored on the evalkit server; "
            "pass a base_url judge to judge on this machine"
        )
