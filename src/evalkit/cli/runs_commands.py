from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
import typer

from evalkit.cli.client import ApiClient, ApiError
from evalkit.client.api import encode
from evalkit.contracts.atif import TrajectoryValidationError
from evalkit.contracts.harness import RunListItem
from evalkit.core.agents import AgentDefinition, Driver, driver
from evalkit.core.harness import HarnessClient
from evalkit.core.paths import UnsafePathError, is_safe_run_id
from evalkit.core.pull import (
    MAX_PULL,
    SCAN_NOTE,
    RunBundle,
    bundle_files,
    index_row,
    list_harness_runs,
    merge_index,
    safe_dir,
)

runs_app = typer.Typer(no_args_is_help=True, help="List and pull an agent's runs (app or eval).")

RELATIVE = re.compile(r"^(\d+)([dhm])$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$")
UNITS = {"d": "days", "h": "hours", "m": "minutes"}
SERVER_PAGE = 200
DEFAULT_ROOT = Path("evalkit-runs")
PULL_HELP = (
    "Download runs with their ATIF trajectories into a local directory: index.json plus "
    "runs/<run_id>/ (run.json, trajectory.json, and for eval runs case.json and "
    "evaluations.json).\n\n"
    "Workspace files and full tool outputs are not fetched: the trajectory keeps the "
    "shortened tool output the agent saw. List and fetch a run's files with "
    "evalkit runs files.\n\n"
    "Writes to ./evalkit-runs/… by default. Add that directory to .gitignore, or pass "
    "--out with a path outside the repo. Pulling again into the same --out adds to it.\n\n"
    "Example: evalkit runs pull ouragent@prod --source app --since 7d --limit 25 --offset 0"
)

FILES_HELP = (
    "List a pulled run's workspace files, or fetch some of them. Without --path it prints "
    "the file tree (size and path) and writes it to runs/<run_id>/files.json. Each --path, "
    "a file or a folder for everything under it, is fetched into "
    "runs/<run_id>/files/<path>, one request per file, each written as it arrives; the "
    "first failure stops the command and names the file. A path not in the tree, such as "
    "a tool result's extra.full_output_path from trajectory.json, is fetched directly.\n\n"
    "Example: evalkit runs files evalkit-runs/ouragent@prod/20261023-101500 RUN_ID "
    "--path outputs/report.md"
)

LocalHarness = Callable[[AgentDefinition], HarnessClient]


def parse_when(text: str | None, now: datetime | None = None) -> datetime | None:
    if text is None:
        return None
    match = RELATIVE.match(text)
    if match:
        return (now or datetime.now(UTC)) - timedelta(**{UNITS[match[2]]: int(match[1])})
    if not ISO.match(text):
        raise ApiError(f"not a time: {text!r}; use ISO 8601 (2026-10-08T00:00Z) or 7d / 12h / 30m")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _list(client: ApiClient, agent: str, params: dict) -> dict:
    query = {k: v for k, v in params.items() if v is not None}
    return client.get(f"/api/agents/{encode(agent)}/runs", params=query).json()


def _list_stored(client: ApiClient, agent: str, filters: dict, wanted: int) -> tuple[list, int]:
    rows: list[dict] = []
    total = 0
    while len(rows) < wanted:
        size = min(SERVER_PAGE, wanted - len(rows))
        body = _list(client, agent, {**filters, "limit": size, "offset": len(rows)})
        rows += body["runs"]
        total = body["total"]
        if len(body["runs"]) < size:
            break
    return rows, total


def _known(client: ApiClient, agent: str, run_ids: list[str]) -> set[str]:
    known: set[str] = set()
    for start in range(0, len(run_ids), MAX_PULL):
        chunk = run_ids[start : start + MAX_PULL]
        body = client.get(
            f"/api/agents/{encode(agent)}/runs", params=[("run_id", i) for i in chunk]
        ).json()
        known |= {row["run_id"] for row in body["runs"]}
    return known


def _unzip(content: bytes) -> tuple[dict, dict[str, bytes]]:
    archive = zipfile.ZipFile(io.BytesIO(content))
    files = {m.filename: archive.read(m) for m in archive.infolist() if not m.is_dir()}
    return json.loads(files.pop("index.json")), files


def _server_pull_ids(client: ApiClient, agent: str, run_ids: list[str]) -> tuple[list, dict]:
    rows: list[dict] = []
    files: dict[str, bytes] = {}
    for start in range(0, len(run_ids), MAX_PULL):
        chunk = run_ids[start : start + MAX_PULL]
        resp = client.post(f"/api/agents/{encode(agent)}/runs/pull", json={"run_ids": chunk})
        index, chunk_files = _unzip(resp.content)
        rows += index["runs"]
        files |= chunk_files
    return rows, files


def write_pull(target: Path, index: dict, files: dict[str, bytes]) -> None:
    target = write_files(target, files)
    existing_path = target / "index.json"
    existing = json.loads(existing_path.read_text()) if existing_path.is_file() else None
    existing_path.write_text(json.dumps(merge_index(existing, index), indent=2) + "\n")


def write_files(target: Path, files: dict[str, bytes]) -> Path:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    # Member names carry run ids and harness-reported paths — validate each one
    # resolves under the destination before writing.
    for name, data in files.items():
        destination = (target / name).resolve()
        if not destination.is_relative_to(target):
            raise ApiError(f"archive member escapes the output directory: {name!r}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return target


def _stamp(target: Path, filters: dict, total: int | None = None) -> None:
    path = target.resolve() / "index.json"
    index = json.loads(path.read_text())
    index["filters"] = filters
    if total is not None:
        index["total_matching"] = total
    path.write_text(json.dumps(index, indent=2) + "\n")


def unignored_in_git(target: Path) -> bool:
    if shutil.which("git") is None:
        return False
    probe = target if target.exists() else target.parent
    while not probe.exists():
        probe = probe.parent
    inside = subprocess.run(
        ["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return False
    ignored = subprocess.run(
        ["git", "-C", str(probe), "check-ignore", "-q", str(target.resolve())],
        capture_output=True,
    )
    return ignored.returncode != 0


async def _harness_rows(
    harness: HarnessClient,
    known: Callable[[list[str]], set[str]],
    since: datetime | None,
    until: datetime | None,
    wanted: int,
) -> tuple[list[RunListItem], int, str | None]:
    async def exclude(run_ids: list[str]) -> set[str]:
        return known(run_ids)

    try:
        listed = await list_harness_runs(
            harness, wanted=wanted, since=since, until=until, exclude=exclude
        )
    finally:
        await harness.aclose()
    return listed.runs, listed.total, SCAN_NOTE if listed.capped else None


def _require_run_id(run_id: str) -> None:
    if not is_safe_run_id(run_id):
        raise ApiError(f"run id {run_id!r} must be one plain path segment")


async def _harness_bundle(harness: HarnessClient, row: dict) -> RunBundle:
    run_id = row["run_id"]
    bundle = RunBundle(
        run_id=run_id,
        source="app",
        created_at=row.get("created_at"),
        title=row.get("title"),
        run={"run_id": run_id, "source": "app"},
    )

    try:
        bundle.trajectory = (await harness.trajectory(run_id)).model_dump(exclude_none=True)
    except (httpx.HTTPError, TrajectoryValidationError, UnsafePathError) as exc:
        bundle.pull_error = str(exc)
    return bundle


async def _harness_bundles(harness: HarnessClient, rows: list[dict]) -> list[RunBundle]:
    bundles = [await _harness_bundle(harness, row) for row in rows]
    await harness.aclose()
    return bundles


def _sort_key(row: dict) -> datetime:
    created = row.get("created_at")
    if not created:
        return datetime.min.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(created)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _local_rows(
    client: ApiClient,
    definition: AgentDefinition,
    harness: LocalHarness,
    filters: dict,
    limit: int,
    offset: int,
) -> tuple[list[dict], int, str | None]:
    agent = definition.ref
    stored, stored_total = _list_stored(client, agent, filters, limit + offset)
    extra: list[dict] = []
    extra_total = 0
    note: str | None = None
    if filters["source"] != "eval":
        try:
            listed, extra_total, note = asyncio.run(
                _harness_rows(
                    harness(definition),
                    lambda run_ids: _known(client, agent, run_ids),
                    parse_when(filters["since"]),
                    parse_when(filters["until"]),
                    limit + offset,
                )
            )
        except httpx.HTTPError as exc:
            listed, note = [], f"harness unreachable: {exc}"
        extra = [
            {**run.model_dump(mode="json"), "source": "app", "stored": False} for run in listed
        ]
    rows = sorted([*stored, *extra], key=_sort_key, reverse=True)
    return rows[offset : offset + limit], stored_total + extra_total, note


def _pull_local(
    client: ApiClient,
    definition: AgentDefinition,
    harness: LocalHarness,
    rows: list[dict],
) -> tuple[dict, dict[str, bytes]]:
    agent = definition.ref
    known = _known(client, agent, [row["run_id"] for row in rows])
    index_rows, files = _server_pull_ids(
        client, agent, [row["run_id"] for row in rows if row["run_id"] in known]
    )
    local = asyncio.run(
        _harness_bundles(harness(definition), [r for r in rows if r["run_id"] not in known])
    )
    for bundle in local:
        files |= bundle_files(bundle)
    return {
        "agent": agent,
        "pulled_at": datetime.now(UTC).isoformat(),
        "runs": index_rows + [index_row(bundle) for bundle in local],
    }, files


def register(
    app: typer.Typer,
    client_factory: Callable[[], ApiClient],
    agent_definition: Callable[[str], AgentDefinition],
    local_harness: LocalHarness,
) -> None:
    app.add_typer(runs_app, name="runs")

    @runs_app.command("list")
    def runs_list(
        agent: str = typer.Argument(..., help="myagent (yours) or ouragent@dev."),
        source: str = typer.Option("all", "--source", help="app, eval or all."),
        since: str | None = typer.Option(None, "--since", help="ISO time, or 7d / 12h / 30m."),
        until: str | None = typer.Option(None, "--until", help="ISO time, or 7d / 12h / 30m."),
        limit: int = typer.Option(50, "--limit"),
        offset: int = typer.Option(0, "--offset"),
        as_json: bool = typer.Option(False, "--json", help="One JSON object per line."),
    ):
        """List an agent's runs: app runs (outside any experiment) and eval runs.

        Example: evalkit runs list ouragent@prod --source app --since 7d
        """
        client = client_factory()
        definition = agent_definition(agent)
        filters = _filters(source, parse_when(since), parse_when(until))
        note = None
        if driver(definition) is Driver.CLI:
            rows, total, note = _local_rows(
                client, definition, local_harness, filters, limit, offset
            )
        else:
            body = _list(client, definition.ref, {**filters, "limit": limit, "offset": offset})
            rows, total, note = body["runs"], body["total"], body.get("harness_note")
        if driver(definition) is Driver.SDK and not definition.is_otel and source != "eval":
            note = (
                f"{definition.ref} has no harness and no traces, so evalkit only sees its eval runs"
            )
        if as_json:
            for row in rows:
                typer.echo(json.dumps(row))
            typer.echo(f"{len(rows)} of {total} run(s)", err=True)
            if note:
                typer.echo(f"note: {note}", err=True)
            return
        for row in rows:
            where = (
                f"exp {row['experiment_id']} {row['case_id']}" if row.get("experiment_id") else ""
            )
            typer.echo(
                f"{row['run_id']:<38} {row['source']:<4} {row.get('status') or '-':<10} "
                f"{row.get('created_at') or '-':<26} {where} {row.get('title') or ''}".rstrip()
            )
        typer.echo(f"{len(rows)} of {total} run(s)")
        if note:
            typer.echo(f"note: {note}")

    @runs_app.command("pull", help=PULL_HELP)
    def runs_pull(
        agent: str | None = typer.Argument(None, help="myagent (yours) or ouragent@dev."),
        source: str = typer.Option("all", "--source", help="app, eval or all."),
        since: str | None = typer.Option(None, "--since", help="ISO time, or 7d / 12h / 30m."),
        until: str | None = typer.Option(None, "--until", help="ISO time, or 7d / 12h / 30m."),
        limit: int | None = typer.Option(
            None,
            "--limit",
            help=f"Runs to pull; default {MAX_PULL}. Above {MAX_PULL} on purpose only.",
        ),
        offset: int = typer.Option(0, "--offset", help="Skip this many matching runs."),
        run_id: list[str] = typer.Option([], "--run-id", help="Pull these runs only."),
        experiment: int | None = typer.Option(None, "--experiment", help="Pull every run of it."),
        out: Path | None = typer.Option(None, "--out", help="Output directory."),
    ):
        client = client_factory()
        if experiment is not None:
            ref = client.get(f"/api/experiments/{experiment}").json()["agent"]
            target = out or DEFAULT_ROOT / f"experiment-{experiment}"
            pulled = 0
            while True:
                resp = client.post(
                    f"/api/agents/{encode(ref)}/runs/pull",
                    json={
                        "experiment_id": experiment,
                        "limit": MAX_PULL,
                        "offset": pulled,
                    },
                )
                index, members = _unzip(resp.content)
                write_pull(target, index, members)
                pulled += len(index["runs"])
                if not index["runs"] or pulled >= index["total_matching"]:
                    break
            _stamp(target, {"experiment": experiment})
            _report(target)
            return
        if agent is None:
            raise ApiError("pass an agent, or --experiment ID")
        definition = agent_definition(agent)
        ref = definition.ref
        start, end = parse_when(since), parse_when(until)
        filters = _filters(source, start, end)
        wanted = limit if limit is not None else MAX_PULL
        target = out or DEFAULT_ROOT / ref / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        local = driver(definition) is Driver.CLI
        total: int | None = None
        if run_id:
            total = len(run_id)
        if run_id and local:
            rows = [{"run_id": i} for i in run_id]
            write_pull(target, *_pull_local(client, definition, local_harness, rows))
        elif run_id:
            index_rows, members = _server_pull_ids(client, ref, run_id)
            write_pull(
                target,
                {"agent": ref, "pulled_at": datetime.now(UTC).isoformat(), "runs": index_rows},
                members,
            )
        elif local:
            rows, total, note = _local_rows(
                client, definition, local_harness, filters, wanted, offset
            )
            local_index, members = _pull_local(client, definition, local_harness, rows)
            write_pull(target, {**local_index, "note": note}, members)
        else:
            done = 0
            while done < wanted:
                size = min(MAX_PULL, wanted - done)
                resp = client.post(
                    f"/api/agents/{encode(ref)}/runs/pull",
                    json={**filters, "limit": size, "offset": offset + done},
                )
                index, members = _unzip(resp.content)
                write_pull(target, index, members)
                total = index["total_matching"]
                done += len(index["runs"])
                if len(index["runs"]) < size:
                    break
        invocation = {
            **filters,
            "limit": wanted,
            "offset": offset,
            "run_ids": run_id or None,
        }
        _stamp(target, invocation, total)
        _report(target)

    @runs_app.command("files", help=FILES_HELP)
    def runs_files(
        directory: Path = typer.Argument(..., help="A directory evalkit runs pull wrote."),
        run_id: str = typer.Argument(..., help="A run in its index.json."),
        path: list[str] = typer.Option(
            [], "--path", help="A file, or a folder for everything under it. Repeatable."
        ),
    ):
        index_path = directory / "index.json"
        if not index_path.is_file():
            raise ApiError(f"{directory} has no index.json; pull runs into it first")
        index = json.loads(index_path.read_text())
        if run_id not in {row["run_id"] for row in index["runs"]}:
            raise ApiError(f"{run_id} is not in {index_path}; pull it first")
        client = client_factory()
        definition = agent_definition(index["agent"])
        files_url = f"/api/agents/{encode(definition.ref)}/runs/{encode(run_id)}/files"
        listing = client.get(files_url).json()["files"]
        stored = {entry["path"] for entry in listing if entry.get("stored")}
        personal = driver(definition) is Driver.CLI
        if personal and not listing:
            listing = asyncio.run(_harness_listing(local_harness(definition), run_id))
        base = f"runs/{safe_dir(run_id)}"
        tree = json.dumps({"files": listing}, indent=2) + "\n"
        write_files(directory, {f"{base}/files.json": tree.encode()})
        if not path:
            for entry in listing:
                size = "-" if entry.get("size") is None else entry["size"]
                typer.echo(f"{size:>12}  {entry['path']}")
            typer.echo(f"{len(listing)} file(s); fetch with --path <file or folder>")
            return

        def save(name: str, data: bytes) -> None:
            write_files(directory, {f"{base}/files/{name}": data})

        picked = _picked(listing, path)
        from_server = [p for p in picked if p in stored or not personal]
        for name in from_server:
            typer.echo(f"fetching {name}")
            save(name, client.get(f"{files_url}/{quote(name, safe='/')}").content)
        from_harness = [p for p in picked if p not in from_server]
        if from_harness:
            asyncio.run(_harness_fetch(local_harness(definition), run_id, from_harness, save))
        target = (directory / base / "files").resolve()
        typer.echo(f"fetched {len(picked)} file(s) to {target}")


def _filters(source: str, since: datetime | None, until: datetime | None) -> dict:
    return {"source": source, "since": _iso(since), "until": _iso(until)}


def _report(target: Path) -> None:
    index = json.loads((target / "index.json").read_text())
    failed = sum(1 for row in index["runs"] if row.get("pull_error"))
    typer.echo(f"pulled {len(index['runs'])} run(s) to {target.resolve()}")
    if index.get("note"):
        typer.echo(f"note: {index['note']}")
    if failed:
        typer.echo(f"{failed} run(s) could not be fetched in full; see pull_error in run.json")
    if unignored_in_git(target):
        typer.secho(
            f"warning: {target} is inside a git repo and not ignored; add it to .gitignore",
            fg="yellow",
            err=True,
        )


def _picked(listing: list[dict], wanted: list[str]) -> list[str]:
    paths = [entry["path"] for entry in listing]
    picked: list[str] = []
    for want in wanted:
        prefix = want.strip("/")
        if not prefix and not paths:
            raise ApiError("this run has no files to fetch")
        matched = [p for p in paths if not prefix or p == prefix or p.startswith(f"{prefix}/")]
        picked += [p for p in matched or [prefix] if p not in picked]
    return picked


async def _harness_listing(harness: HarnessClient, run_id: str) -> list[dict]:
    _require_run_id(run_id)
    try:
        if not (await harness.capabilities()).provides.workspace:
            raise ApiError("this harness does not expose workspace files")
        return (await harness.list_files(run_id)).model_dump()["files"]
    finally:
        await harness.aclose()


async def _harness_fetch(
    harness: HarnessClient, run_id: str, paths: list[str], save: Callable[[str, bytes], None]
) -> None:
    _require_run_id(run_id)
    try:
        for name in paths:
            typer.echo(f"fetching {name}")
            data = await harness.find_file(run_id, name)
            if data is None:
                raise ApiError(f"{name}: no such file on the harness")
            save(name, data)
    finally:
        await harness.aclose()
