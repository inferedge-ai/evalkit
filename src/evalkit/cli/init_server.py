import importlib.metadata
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml
from dotenv import dotenv_values

from evalkit import version
from evalkit.cli.client import ApiError
from evalkit.paths import skills_root

DEFAULT_DIR = Path("evalkit-try")
ENCRYPTION_KEY_ENV = "EVALKIT_ENCRYPTION_KEY"
DEFAULT_DATABASE_URL = "postgresql+psycopg://localhost/evalkit"
DEFAULT_ADMIN_PASSWORD = "evalkit-admin"
KIT_REQUIREMENT = '"evalkit-py[otel]"'
KIT_LEAVES_OUT = ("*.whl", "__pycache__", "*.pyc", ".env")
DOCKER_ONLY = ("docker-compose.yml", ".env.example")


@dataclass(frozen=True)
class Question:
    key: str
    env: str
    prompt: str
    default: str | None = None
    secret: bool = False

    @property
    def flag(self) -> str:
        return f"--{self.key.replace('_', '-')}"


QUESTIONS = (
    Question(
        "database_url",
        "EVALKIT_DATABASE_URL",
        "Postgres URL: where evalkit stores users, cases, experiments and scores "
        "(the database is created if missing)",
        DEFAULT_DATABASE_URL,
    ),
    Question(
        "llm_base_url",
        "LLM_BASE_URL",
        "LLM base URL: an OpenAI-compatible endpoint the judge and the sample agent call "
        "(e.g. https://api.openai.com/v1)",
    ),
    Question(
        "llm_model",
        "LLM_MODEL",
        "LLM model: the model name at that endpoint (e.g. gpt-4o-mini)",
    ),
    Question(
        "llm_api_key",
        "LLM_API_KEY",
        "LLM API key: kept in .env, never in config.yaml",
        secret=True,
    ),
    Question(
        "admin_password",
        "EVALKIT_ADMIN_PASSWORD",
        (
            "Admin password: you log in to the web UI and the CLI as `admin` with it; "
            "admin can add users and workspaces and change any setting"
        ),
        DEFAULT_ADMIN_PASSWORD,
        secret=True,
    ),
)

DATABASE_NOTICE = (
    "evalkit init-server creates the database named in the Postgres URL on that Postgres "
    "server if it does not exist yet, and creates evalkit's tables in it."
)


@dataclass(frozen=True)
class Answers:
    database_url: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    admin_password: str


def resolve(flags: dict[str, str | None], *, yes: bool) -> Answers:
    values = {}
    for question in QUESTIONS:
        given = flags.get(question.key)
        if given is not None:
            values[question.key] = given
            continue
        if yes:
            if question.default is None:
                raise ApiError(f"{question.flag} (or {question.env}) is required with --yes")
            values[question.key] = question.default
            continue
        values[question.key] = typer.prompt(
            question.prompt,
            default=question.default,
            hide_input=question.secret and sys.stdin.isatty(),
        )
    return Answers(**values)


def kit_root() -> Path:
    return skills_root() / "evalkit-deployment" / "try"


def _direct_url() -> dict:
    text = importlib.metadata.distribution(version.DISTRIBUTION).read_text("direct_url.json")
    return json.loads(text) if text else {}


def pinned_requirement() -> str:
    direct = _direct_url()
    if direct.get("dir_info", {}).get("editable"):
        return f"evalkit-py[otel] @ {direct['url']}"
    return f"evalkit-py[otel]=={version.current()}"


def kit_files_in(target: Path) -> list[str]:
    return sorted(path.name for path in kit_root().iterdir() if (target / path.name).exists())


def copy_kit(target: Path, *, docker: bool) -> None:
    left_out = KIT_LEAVES_OUT if docker else (*KIT_LEAVES_OUT, *DOCKER_ONLY)
    shutil.copytree(
        kit_root(), target, ignore=shutil.ignore_patterns(*left_out), dirs_exist_ok=True
    )
    run_py = target / "run.py"
    run_py.write_text(run_py.read_text().replace(KIT_REQUIREMENT, f'"{pinned_requirement()}"'))


def server_config(database_url: str, data_dir: Path) -> dict:
    data = yaml.safe_load((kit_root() / "config.yaml").read_text())
    data["db"] = {"url": database_url}
    data["blob"] = {"url": data_dir.resolve().as_uri()}
    return data


def existing_encryption_key(target: Path) -> str | None:
    return dotenv_values(target / ".env").get(ENCRYPTION_KEY_ENV)


def write_server_files(target: Path, answers: Answers, encryption_key: str | None) -> None:
    config = server_config(answers.database_url, target / "data")
    (target / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    env = target / ".env"
    env.write_text(
        f"LLM_BASE_URL={answers.llm_base_url}\n"
        f"LLM_MODEL={answers.llm_model}\n"
        f"LLM_API_KEY={answers.llm_api_key}\n"
        f"EVALKIT_ADMIN_PASSWORD={answers.admin_password}\n"
        + (
            f"{ENCRYPTION_KEY_ENV}={encryption_key}\n"
            if encryption_key
            else f"# {ENCRYPTION_KEY_ENV}=<the encryption_key this database was set up with>\n"
        )
    )
    env.chmod(0o600)
