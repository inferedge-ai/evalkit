from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from evalkit.client.api import SyncApi
from evalkit.contracts.cases import REGISTRY_FILENAME, is_case_yaml, load_cases_from_yaml
from evalkit.core.fixtures import load_registries, sha256_hex
from evalkit.core.workspace import Reference, parse_reference
from evalkit.store.base import GitRevision, Selection


@dataclass
class ResolvedPath:
    ref: Reference
    dataset_dir: Path | None
    selection: Selection
    subpath: str | None = None


def _case_ids(file: Path) -> list[str]:
    cases, _ = load_cases_from_yaml(file)
    return [case.id for case in cases]


def _selection_for(subpath: str, dataset_dir: Path | None) -> Selection:
    if not subpath:
        return Selection(all=True)
    on_disk = dataset_dir / subpath if dataset_dir is not None else None
    if on_disk is not None and on_disk.is_dir():
        return Selection(tag=on_disk.name)
    if on_disk is not None and on_disk.is_file():
        return Selection(case_ids=_case_ids(on_disk))
    if Path(subpath).suffix in (".yaml", ".yml"):
        raise ValueError(f"{subpath} is not on disk; selecting a file needs its case ids")
    return Selection(tag=Path(subpath).name)


def _on_disk(candidate: Path, cases_root: Path | None) -> tuple[Path, str]:
    resolved = candidate.resolve()
    if cases_root is not None and resolved.is_relative_to(cases_root.resolve()):
        relative = resolved.relative_to(cases_root.resolve())
        if not relative.parts:
            raise ValueError(f"{candidate} is the cases root; pass a dataset directory under it")
        return cases_root.resolve() / relative.parts[0], Path(*relative.parts[1:]).as_posix()
    if resolved.is_dir():
        return resolved, ""
    return resolved.parent, resolved.name


def resolve_path(arg: str, cases_root: Path | None, my_workspace: str) -> ResolvedPath:
    candidate = Path(arg)
    if candidate.exists():
        dataset_dir, subpath = _on_disk(candidate, cases_root)
        ref = Reference(dataset_dir.name, my_workspace)
        if subpath == ".":
            subpath = ""
        return ResolvedPath(ref, dataset_dir, _selection_for(subpath, dataset_dir), subpath or None)
    ref_text, _, subpath = arg.partition("/")
    ref = parse_reference(ref_text, my_workspace)
    own = ref.workspace == my_workspace
    if own and cases_root is None:
        raise ValueError(
            f"{ref.name} is in your workspace, so it syncs from disk: set `cases:` in "
            "evalkit.yaml, or pass the dataset's directory"
        )
    on_disk = cases_root / ref.name if cases_root is not None else None
    if on_disk is not None and not on_disk.is_dir():
        on_disk = None
    if own and on_disk is None:
        raise ValueError(f"no dataset directory {ref.name!r} under {cases_root}")
    return ResolvedPath(
        ref, on_disk if own else None, _selection_for(subpath, on_disk), subpath or None
    )


def resolve_dataset(dataset: str, workspace: str | None, my_workspace: str) -> Reference:
    ref = parse_reference(dataset, my_workspace)
    if "@" in dataset and workspace:
        raise ValueError("dataset already names a workspace; drop the workspace argument")
    if workspace:
        return Reference(ref.name, workspace)
    return ref


def _git(args: list[str], cwd: Path) -> str | None:
    if shutil.which("git") is None:
        return None
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def git_revision(path: Path) -> GitRevision:
    cwd = path if path.is_dir() else path.parent
    sha = _git(["rev-parse", "HEAD"], cwd)
    if sha is None:
        return GitRevision()
    status = _git(["status", "--porcelain", "--", str(path)], cwd)
    return GitRevision(
        sha=sha, branch=_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd), dirty=bool(status)
    )


def _nearest_registry(start: Path) -> Path | None:
    for directory in (start, *start.parents):
        candidate = directory / REGISTRY_FILENAME
        if candidate.is_file():
            return candidate
    return None


def sync_files(dataset_dir: Path, subpath: str | None = None) -> list[tuple[str, Path]]:
    start = dataset_dir / subpath if subpath else dataset_dir
    if not start.exists():
        raise ValueError(
            f"{subpath} is not a folder or file under {dataset_dir}; sync needs one on disk"
        )
    if start.is_dir():
        files = sorted(
            f
            for pattern in ("*.yaml", "*.yml")
            for f in start.rglob(pattern)
            if f.is_file()
            and not any(part.startswith(".") for part in f.relative_to(dataset_dir).parts)
            and is_case_yaml(f.relative_to(dataset_dir))
        )
    else:
        files = [start]
    out = [(f.relative_to(dataset_dir).as_posix(), f) for f in files]
    registry = _nearest_registry(dataset_dir)
    if registry is not None:
        out.append((REGISTRY_FILENAME, registry))
    return out


def upload_fixtures(
    client: SyncApi, files: list[tuple[str, Path]], fixtures_dir: Path
) -> list[str]:
    registry = [path for name, path in files if name == REGISTRY_FILENAME]
    entries, _ = load_registries(registry)
    referenced: set[str] = set()
    for name, path in files:
        if name == REGISTRY_FILENAME:
            continue
        cases, _ = load_cases_from_yaml(path)
        referenced.update(key for case in cases for key in case.fixtures)
    uploaded: list[str] = []
    for key in sorted(referenced):
        entry = entries.get(key)
        if entry is None:
            continue
        url = f"/api/fixtures/sha256/{entry.sha256}"
        if client.head(url).status_code == 200:
            continue
        local = fixtures_dir / key
        if not local.is_file():
            raise ValueError(
                f"fixture {key!r} is registered but {local} does not exist and the server has "
                f"no bytes with sha256 {entry.sha256}"
            )
        data = local.read_bytes()
        if sha256_hex(data) != entry.sha256:
            raise ValueError(
                f"fixture {key!r} on disk has sha256 {sha256_hex(data)} but {REGISTRY_FILENAME} "
                f"says {entry.sha256}; update the registry entry"
            )
        client.put(url, content=data, retry=True)
        uploaded.append(key)
    return uploaded


def sync_dataset(
    client: SyncApi,
    dataset_dir: Path,
    *,
    name: str,
    workspace: str,
    revision: GitRevision,
    subpath: str | None = None,
    fixtures_dir: Path | None = None,
    actor: str | None = None,
) -> dict:
    params: dict[str, object] = {
        "dataset": name,
        "workspace": workspace,
        "prune": subpath is None,
    }
    for key, value in revision.model_dump().items():
        if value is not None:
            params[key] = value
    if actor is not None:
        params["actor"] = actor
    files = sync_files(dataset_dir, subpath)
    if fixtures_dir is not None:
        upload_fixtures(client, files, fixtures_dir)
    return client.post_files("/api/sync", files, params=params).json()
