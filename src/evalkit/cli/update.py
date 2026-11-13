import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import typer

from evalkit import version
from evalkit.cli import skill_targets
from evalkit.cli.client import ApiClient, ApiError
from evalkit.cli.server_extra import has_server_extra

PINNED = re.compile(r'"(evalkit-py(?:\[[^\]]*\])?)==([^"]+)"')
VENDORED = re.compile(r"evalkit-[^\s\"']+\.whl")


def install_command(prefix: Path, requirement: str) -> list[str] | None:
    parts = prefix.parts
    if "uv" in parts and "tools" in parts:
        return ["uv", "tool", "install", "--reinstall", requirement]
    if "pipx" in parts and "venvs" in parts:
        return ["pipx", "install", "--force", requirement]
    return None


def installed_cli(prefix: Path) -> Path:
    return prefix / ("Scripts" if os.name == "nt" else "bin") / "evalkit"


def _run_checked(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _run_for_exit_code(command: list[str]) -> int:
    return subprocess.run(command).returncode


def update_project_pin(cwd: Path, target: str, run: Callable[[list[str]], object]) -> bool:
    pyproject = cwd / "pyproject.toml"
    if not pyproject.is_file():
        return False
    text = pyproject.read_text()
    if PINNED.search(text):
        moved = PINNED.sub(lambda m: f'"{m.group(1)}=={target}"', text)
        if moved != text:
            pyproject.write_text(moved)
            run(["uv", "sync"])
            typer.echo(f"updated {pyproject}")
            return True
    elif VENDORED.search(text):
        typer.echo("this project uses a vendored evalkit wheel; move it to PyPI:")
        typer.echo(f'  uv remove evalkit && uv add "{version.DISTRIBUTION}=={target}"')
    return False


def run_update(
    client: ApiClient,
    *,
    prefix: Path | None = None,
    run: Callable[[list[str]], object] = _run_checked,
    run_skills_install: Callable[[list[str]], object] = _run_for_exit_code,
    cwd: Path | None = None,
    skills: bool = True,
    targets_path: Path | None = None,
    server_extra: bool = has_server_extra(),
) -> None:
    target = client.get("/api/cli").json()["version"]
    project = cwd or Path.cwd()
    synced = update_project_pin(project, target, run)
    local = version.current()
    if local == target:
        typer.echo(f"evalkit {local} is already current")
        return
    install_prefix = prefix or Path(sys.prefix)
    if synced and install_prefix.resolve() == (project / ".venv").resolve():
        typer.echo(f"installed evalkit {target} into {install_prefix}")
        return
    extra = "[server]" if server_extra else ""
    requirement = f"{version.DISTRIBUTION}{extra}=={target}"
    command = install_command(install_prefix, requirement)
    if command is None:
        typer.echo(f'  uv tool install --reinstall "{requirement}"')
        typer.echo(f'  pipx install --force "{requirement}"')
        raise ApiError("could not tell how evalkit was installed; run one of the commands above")
    run(command)
    typer.echo(f"installed evalkit {target}")
    if skills:
        failed = skill_targets.refresh(
            project, installed_cli(install_prefix), run_skills_install, targets_path
        )
        if failed:
            raise ApiError(
                f"skills not fully refreshed in {', '.join(str(path) for path in failed)}; "
                "see the lines above for what to do in each"
            )
