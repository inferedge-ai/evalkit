import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, field_validator

Kind = Literal["agent", "evaluator", "judge"]


class MissingCredential(ValueError):
    pass


ENV_REF = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")


class CredentialsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    agents: dict[str, str] = {}
    evaluators: dict[str, str] = {}
    judges: dict[str, str] = {}

    @field_validator("agents", "evaluators", "judges")
    @classmethod
    def _env_refs_only(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [k for k, v in value.items() if not ENV_REF.match(v)]
        if bad:
            raise ValueError(f"credentials must be env:NAME references (no defaults): {bad}")
        return value

    @field_validator("judges")
    @classmethod
    def _judge_keys_are_urls_or_env_refs(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [k for k in value if k.startswith("env:") and not ENV_REF.match(k)]
        if bad:
            raise ValueError(f"a credentials.judges key is a base URL or env:NAME: {bad}")
        return value


class CredentialRegistry:
    def __init__(
        self, cfg: CredentialsConfig, environ: Mapping[str, str], *, home: str | None = None
    ):
        self._cfg = cfg
        self._env = environ
        self._home = home

    def _table(self, kind: Kind) -> dict[str, str]:
        if kind == "judge":
            return {
                url: v
                for k, v in self._cfg.judges.items()
                if (url := self._judge_url(k)) is not None
            }
        return {"agent": self._cfg.agents, "evaluator": self._cfg.evaluators}[kind]

    def _judge_url(self, key: str) -> str | None:
        if not key.startswith("env:"):
            return key
        return self._env.get(key.removeprefix("env:"))

    def _unset_judge_keys(self) -> list[str]:
        return [k for k in self._cfg.judges if k.startswith("env:") and self._judge_url(k) is None]

    def _entry(self, kind: Kind, ref: str) -> str | None:
        table = self._table(kind)
        if ref in table:
            return table[ref]
        name, _, workspace = ref.partition("@")
        if kind != "judge" and self._home is not None and workspace == self._home:
            return table.get(name)
        return None

    def key_for(self, kind: Kind, ref: str) -> str | None:
        entry = self._entry(kind, ref)
        if entry is None:
            return None
        name = entry.removeprefix("env:")
        if name not in self._env:
            raise MissingCredential(f"credentials.{kind}s[{ref!r}] needs env var {name}")
        return self._env[name]

    def require_judge_key(self, base_url: str) -> str:
        key = self.key_for("judge", base_url)
        if key is None:
            unset = self._unset_judge_keys()
            hint = f" (unset: {', '.join(k.removeprefix('env:') for k in unset)})" if unset else ""
            raise MissingCredential(f"no credentials.judges entry for {base_url}{hint}")
        return key


def load_dotenv_beside(config_path: Path) -> dict[str, str]:
    file_values = {
        k: v for k, v in dotenv_values(config_path.parent / ".env").items() if v is not None
    }
    return {**file_values, **os.environ}
