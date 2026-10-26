import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import typer

from evalkit import version
from evalkit.cli import skill_targets
from evalkit.cli.client import ApiClient, ApiError

DEFAULT_VENDOR_DIR = "vendor"


def install_command(prefix: Path, wheel: Path) -> list[str] | None:
    parts = prefix.parts
    if "uv" in parts and "tools" in parts:
        return ["uv", "tool", "install", "--reinstall", str(wheel)]
    if "pipx" in parts and "venvs" in parts:
        return ["pipx", "install", "--force", str(wheel)]
    return None


def installed_cli(prefix: Path) -> Path:
    return prefix / ("Scripts" if os.name == "nt" else "bin") / "evalkit"


def _run_checked(command: list[str]) -> None:
    subprocess.run(command, check=True)


def vendored_wheel(vendor: Path) -> Path | None:
    wheels = sorted(vendor.glob("evalkit-*.whl")) if vendor.is_dir() else []
    return wheels[0] if wheels else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _replace_vendored(
    cwd: Path, old: Path, wheel: Path, run: Callable[[list[str]], object]
) -> bool:
    target = old.parent / wheel.name
    shutil.copy(wheel, target)
    if target != old:
        old.unlink()
    pyproject = cwd / "pyproject.toml"
    synced = pyproject.is_file() and old.name in pyproject.read_text()
    if synced:
        pyproject.write_text(pyproject.read_text().replace(old.name, wheel.name))
        run(["uv", "sync"])
    typer.echo(f"updated {target}")
    return synced


def _vendor_first_time(cwd: Path, vendor: Path, wheel: Path) -> None:
    vendor.mkdir(parents=True, exist_ok=True)
    target = vendor / wheel.name
    shutil.copy(wheel, target)
    shown = Path(os.path.relpath(target.resolve(), cwd.resolve())).as_posix()
    shown = shown if shown.startswith(".") else f"./{shown}"
    typer.echo(f"downloaded {target}")
    typer.echo(f'add it to the project:     uv add "{shown}[otel]"')
    typer.echo(f'or run one script with it: uv run --with "{shown}[otel]" python eval/run.py')


def run_update(
    client: ApiClient,
    *,
    vendor: Path | None = None,
    prefix: Path | None = None,
    run: Callable[[list[str]], object] = _run_checked,
    cwd: Path | None = None,
    skills: bool = True,
    targets_path: Path | None = None,
) -> None:
    info = client.get("/api/cli").json()
    project = cwd or Path.cwd()
    vendor_dir = vendor if vendor is not None else project / DEFAULT_VENDOR_DIR
    vendored = vendored_wheel(vendor_dir)
    local = version.current()
    cli_stale = version.compare(local, info["version"]) is not version.VersionComparison.SAME
    vendor_stale = (vendor is not None and vendored is None) or (
        vendored is not None and _sha256(vendored) != info["sha256"]
    )
    if not cli_stale and not vendor_stale:
        typer.echo(f"evalkit {local} is already current")
        return
    with TemporaryDirectory() as tmp:
        wheel = Path(tmp) / info["filename"]
        wheel.write_bytes(client.get("/api/cli/wheel").content)
        actual = _sha256(wheel)
        if actual != info["sha256"]:
            raise ApiError(f"checksum mismatch: server says {info['sha256']}, downloaded {actual}")
        synced = False
        if vendor_stale and vendored is None:
            _vendor_first_time(project, vendor_dir, wheel)
        elif vendor_stale and vendored is not None:
            synced = _replace_vendored(project, vendored, wheel, run)
        if not cli_stale:
            return
        install_prefix = prefix or Path(sys.prefix)
        command = install_command(install_prefix, wheel)
        if command is None:
            if synced:
                return
            kept = project / wheel.name
            shutil.copy(wheel, kept)
            typer.echo(f"downloaded {kept}")
            typer.echo(f"  uv tool install --reinstall {kept}")
            typer.echo(f"  pipx install --force {kept}")
            raise ApiError(
                "could not tell how evalkit was installed; run one of the commands above"
            )
        run(command)
    typer.echo(f"installed evalkit {info['version']}")
    if skills:
        skill_targets.refresh(project, installed_cli(install_prefix), run, targets_path)
