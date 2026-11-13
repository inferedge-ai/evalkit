from collections.abc import Callable
from pathlib import Path

import typer

INSTALL_MANIFEST = ".evalkit-installed"
TARGETS_PATH = Path.home() / ".evalkit" / "skills-targets"
DEFAULT_TARGETS = (Path(".agents/skills"), Path(".claude/skills"))


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


def find_default_siblings(target: Path) -> list[Path]:
    root = target.parent.parent
    if not any(target == (root / candidate).resolve() for candidate in DEFAULT_TARGETS):
        return [target]
    return [(root / candidate).resolve() for candidate in DEFAULT_TARGETS]


def refresh(
    cwd: Path, cli: Path, run: Callable[[list[str]], object], path: Path | None = None
) -> list[Path]:
    registry = path or TARGETS_PATH
    targets = _read(registry)
    for candidate in DEFAULT_TARGETS:
        found = (cwd / candidate).resolve()
        if (found / INSTALL_MANIFEST).is_file() and found not in targets:
            targets.append(found)
    kept: list[Path] = []
    failed: list[Path] = []
    for target in targets:
        if not (target / INSTALL_MANIFEST).is_file():
            continue
        for directory in find_default_siblings(target):
            if directory in kept:
                continue
            kept.append(directory)
            if run([str(cli), "skills", "install", "--target", str(directory)]):
                failed.append(directory)
            else:
                typer.echo(f"refreshed skills in {directory}; commit them if the repo tracks them")
    _write(registry, kept)
    return failed
