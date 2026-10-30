from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_REPO = _PACKAGE.parents[1]


def _bundled(name: str) -> Path:
    repo = _REPO / name
    if repo.is_dir():
        return repo
    return _PACKAGE / "bundled" / name


def skills_root() -> Path:
    return _bundled("skills")


def contracts_root() -> Path:
    return _bundled("contracts")


def docs_root() -> Path:
    repo = _REPO / "docs" / "user"
    if repo.is_dir():
        return repo
    return _PACKAGE / "bundled" / "docs"


def ui_root() -> Path:
    repo = _REPO / "ui" / "dist"
    if repo.is_dir():
        return repo
    return _PACKAGE / "bundled" / "ui"
