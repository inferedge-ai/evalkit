from collections.abc import Callable
from pathlib import Path

import typer

INSTALL_MANIFEST = ".evalkit-installed"
TARGETS_PATH = Path.home() / ".evalkit" / "skills-targets"
DEFAULT_TARGETS = (Path(".claude/skills"), Path(".agents/skills"))


def _read(path: Path) -> list[Path]:
    return [Path(line) for line in path.read_text().split("\n") if line] if path.is_file() else []


def _write(path: Path, targets: list[Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{target}\n" for target in targets))


def record(target: Path, path: Path | None = None) -> None:
    registry = path or TARGETS_PATH
    resolved = target.resolve()
    targets = _read(registry)
    if resolved not in targets:
        _write(registry, [*targets, resolved])


def refresh(
    cwd: Path, cli: Path, run: Callable[[list[str]], object], path: Path | None = None
) -> None:
    registry = path or TARGETS_PATH
    targets = _read(registry)
    for candidate in DEFAULT_TARGETS:
        found = (cwd / candidate).resolve()
        if (found / INSTALL_MANIFEST).is_file() and found not in targets:
            targets.append(found)
    kept = []
    for target in targets:
        if (target / INSTALL_MANIFEST).is_file():
            run([str(cli), "skills", "install", "--target", str(target)])
            typer.echo(f"refreshed skills in {target}; commit them if the repo tracks them")
            kept.append(target)
    _write(registry, kept)
