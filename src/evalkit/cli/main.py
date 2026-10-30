import asyncio
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from evalkit import version
from evalkit.cli import init_server
from evalkit.cli.admin_commands import register as register_admin
from evalkit.cli.admin_commands import trace_sources_app
from evalkit.cli.client import ApiClient, ApiError
from evalkit.cli.local_run import (
    check_local,
    evaluate_local,
    local_input_schema,
    mark_interrupted,
    run_local,
)
from evalkit.cli.local_run import (
    credentials as local_credentials,
)
from evalkit.cli.runs_commands import register as register_runs
from evalkit.cli.skill_targets import INSTALL_MANIFEST
from evalkit.cli.skill_targets import record as record_skill_target
from evalkit.cli.tokens_commands import register as register_tokens
from evalkit.cli.traces_commands import register as register_traces
from evalkit.cli.update import run_update
from evalkit.client import connection
from evalkit.client.agents import definition_of
from evalkit.client.api import ApiRequestError, VersionMismatch, encode
from evalkit.client.summary import summary_lines
from evalkit.client.sync import git_revision, resolve_path, sync_dataset
from evalkit.config import Config, LocalConfig, MissingEnvError, load_config
from evalkit.contracts.cases import REGISTRY_FILENAME, is_case_yaml, load_cases_from_yaml
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.agents import AgentDefinition, Driver, driver, explicit, server_may_drive
from evalkit.core.fixtures import load_registries
from evalkit.core.harness import HarnessClient
from evalkit.core.lint import fixture_findings
from evalkit.core.lint import lint as lint_cases
from evalkit.core.review import CaseState
from evalkit.core.runner import ScoringFailed, dropped_cases_text
from evalkit.core.workspace import Reference, parse_reference, slug
from evalkit.credentials import MissingCredential
from evalkit.paths import contracts_root, docs_root, skills_root
from evalkit.store.base import GitRevision, Selection

# `serve` and `db upgrade` need the `evalkit[server]` extra; the base install
# is a thin HTTP client. The boundary is explicit here rather than hidden in
# per-command imports.
if find_spec("uvicorn") and find_spec("sqlalchemy"):
    from evalkit.cli import server_commands
else:
    server_commands = None  # type: ignore[assignment]

RUN_POLL_SECONDS = 2.0

app = typer.Typer(
    name="evalkit",
    no_args_is_help=True,
    epilog="Bugs, feature requests and feedback: https://github.com/inferedge-ai/evalkit/issues",
)
datasets_app = typer.Typer(no_args_is_help=True, help="List datasets and their cases.")
harness_app = typer.Typer(no_args_is_help=True, help="Verify a client's Harness API.")
users_app = typer.Typer(no_args_is_help=True, help="Manage server users (admin only).")
skills_app = typer.Typer(no_args_is_help=True, help="Install the bundled agent skills.")
contracts_app = typer.Typer(no_args_is_help=True, help="Export the bundled API contracts.")
db_app = typer.Typer(no_args_is_help=True, help="Server database schema upgrades (server only).")
review_app = typer.Typer(no_args_is_help=True, help="Request, give and read case reviews.")
app.add_typer(datasets_app, name="datasets")
app.add_typer(harness_app, name="harness")
app.add_typer(users_app, name="users")
app.add_typer(skills_app, name="skills")
app.add_typer(contracts_app, name="contracts")
app.add_typer(db_app, name="db")
app.add_typer(review_app, name="review")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(version.current())
        raise typer.Exit()


@app.callback()
def main(
    show_version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Print the CLI version."
    ),
) -> None:
    """evalkit — self-hosted agent evals. Runs execute here against your local agent;
    results land on the server."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _active_connection() -> connection.Connection:
    try:
        return connection.active()
    except LookupError as exc:
        raise ApiError(str(exc)) from exc


def _client() -> ApiClient:
    active = _active_connection()
    return ApiClient(active.url, active.token)


def _connection() -> connection.Connection:
    active = _active_connection()
    me = _client().get("/api/auth/me").json()
    warning = connection.token_expiry_warning(active, me.get("token_expires_at"), datetime.now(UTC))
    if warning is not None:
        typer.secho(warning, fg="yellow", err=True)
    return active._replace(username=me["username"])


def _agent_slug(text: str) -> str:
    return slug(_parse(text, _connection().username or ""))


register_tokens(app, _client)
register_admin(app, _client, lambda: _connection().username or "")
register_traces(app, _client, _agent_slug)
register_runs(
    app,
    lambda: _client(),
    lambda text: _agent_definition(_parse(text, _connection().username or "")),
    lambda definition: HarnessClient(
        definition,
        local_credentials(_with_secrets(_local_config(), scoring=False), _connection()).key_for(
            "agent", definition.ref
        ),
    ),
)


def _parse(text: str, workspace: str) -> Reference:
    try:
        return parse_reference(text, workspace)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _resolve(arg: str, cases_root: Path | None, workspace: str):
    try:
        return resolve_path(arg, cases_root, workspace)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _find_local() -> LocalConfig | None:
    try:
        return LocalConfig.find(Path.cwd())
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _local_config() -> LocalConfig:
    local = _find_local()
    if local is None:
        raise ApiError("no evalkit.yaml found above the current directory")
    return local


def _with_secrets(local: LocalConfig, *, scoring: bool) -> LocalConfig:
    try:
        return local.resolve_env(scoring=scoring)
    except MissingEnvError as exc:
        raise ApiError(str(exc)) from exc


def _server_config(path: Path) -> Config:
    load_dotenv(path.parent / ".env", override=False)
    try:
        return load_config(path)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc


def _relative_files(
    path: Path, patterns: tuple[str, ...], *, allow_empty: bool = False
) -> list[tuple[str, Path]]:
    if path.is_dir():
        matched = sorted(
            f
            for pattern in patterns
            for f in path.rglob(pattern)
            if f.is_file() and not any(part.startswith(".") for part in f.relative_to(path).parts)
        )
        files = [(f.relative_to(path).as_posix(), f) for f in matched]
    else:
        files = [(path.name, path)]
    if not allow_empty and not any(name.endswith((".yaml", ".yml")) for name, _ in files):
        raise ApiError(f"no YAML files under {path}")
    return files


def _yaml_files(path: Path, *, allow_empty: bool = False) -> list[tuple[str, Path]]:
    files = _relative_files(path, ("*.yaml", "*.yml"), allow_empty=allow_empty)
    if path.is_dir():
        files = [(name, p) for name, p in files if is_case_yaml(p.relative_to(path))]
    return files


def _nearest_registry(path: Path) -> Path | None:
    start = path if path.is_dir() else path.parent
    for directory in (start, *start.parents):
        candidate = directory / REGISTRY_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _print_findings(findings: list[dict]) -> None:
    for f in findings:
        marker = "✗" if f["severity"] == "error" else "⚠"
        typer.echo(f"  {marker} {f['code']} [{f['case_id']}] {f['message']}")


@app.command()
def login(
    url: str = typer.Option(None, "--url", help="Server base URL, e.g. https://evalkit.internal"),
    username: str = typer.Option(None, "--username"),
    password: str = typer.Option(None, "--password"),
):
    """Log in to a server and store the login in ~/.evalkit/config.toml, replacing any
    earlier one.

    Example: evalkit login --url https://evalkit.example.com
    """
    url = url or typer.prompt("Server URL")
    username = username or typer.prompt("Username")
    password = password or typer.prompt("Password", hide_input=True)
    body = (
        ApiClient(url)
        .post("/api/auth/login", json={"username": username, "password": password})
        .json()
    )
    path = connection.save(connection.Connection(url, body["token"], body["username"]))
    typer.echo(f"logged in as {username} on {url}; saved to {path}")


@app.command()
def whoami():
    """Check the stored token: who it logs in as and when it expires.

    Exits 1 when the server rejects the token. Run it before a long run or
    before handing the token to an exporter. Example: evalkit whoami
    """
    active = _active_connection()
    me = _client().get("/api/auth/me").json()
    source = " (from EVALKIT_SERVER_URL)" if active.from_env else ""
    typer.echo(f"{me['username']} on {active.url}{source}")
    expires_at = me.get("token_expires_at")
    if expires_at is None:
        typer.echo("token does not expire")
    else:
        expires = datetime.fromisoformat(expires_at).astimezone(UTC)
        typer.echo(f"token expires {expires:%Y-%m-%d %H:%M} UTC")
    warning = connection.token_expiry_warning(active, expires_at, datetime.now(UTC))
    if warning is not None:
        typer.secho(warning, fg="yellow", err=True)


@app.command()
def update(
    server: str = typer.Option(
        None, "--server", help="Server base URL; defaults to the one you logged in to."
    ),
    skills: bool = typer.Option(
        True,
        "--skills/--no-skills",
        help="After reinstalling, refresh the skills in every directory "
        "`evalkit skills install` wrote to.",
    ),
):
    """Reinstall the CLI from PyPI at the server's version.

    Run it from the project directory: it also moves an `evalkit-py==…` pin in
    ./pyproject.toml to the server's version and runs `uv sync`, so the CLI and the SDK
    match the server again after a deploy. After a reinstall it runs the new
    `evalkit skills install` again in every skills directory installed before, and in
    ./.claude/skills or ./.agents/skills if an earlier install put skills there.

    Example: evalkit update · evalkit update --no-skills
    """
    server_url = server or _active_connection().url
    run_update(ApiClient(server_url, check_version=False), skills=skills)


@app.command()
def lint(
    path: Path = typer.Argument(..., help="Case YAML file or directory."),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Lint locally without a server: the no-server subset of the server lint. "
        "Skips the checks that need server state (registered custom evaluators, "
        "overlay targets).",
    ),
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        help="Dataset these cases sync into. With it, the server also checks overlay "
        "targets against the other datasets in its workspace.",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Agent to check case inputs against (its input_schema). Needs the server; "
        "on demand, not for CI.",
    ),
):
    """Lint case YAML without importing it (server-side by default).

    The server lint also checks fixture keys against the nearest fixtures.yaml,
    so it covers everything --offline does.

    Example: evalkit lint ./eval-cases --dataset client-cases · evalkit lint --offline datasets
    · evalkit lint ./eval-cases --agent support-triage
    """
    if offline and agent is not None:
        raise ApiError("--agent needs the server; drop --offline")
    if offline:
        all_cases, all_overlays = [], []
        for _, file_path in _yaml_files(path):
            cases, overlays = load_cases_from_yaml(file_path)
            all_cases.extend(cases)
            all_overlays.extend(overlays)
        known = {name for case in all_cases for name in case.evaluators.custom}
        known.update(name for ov in all_overlays for name in ov.evaluators.custom)
        findings = lint_cases(all_cases, all_overlays, known_custom_evaluators=known)
        registry = _nearest_registry(path)
        entries, registry_findings = load_registries([registry] if registry else [])
        findings.extend(registry_findings)
        registry_in_scope = registry is not None and registry.parent == path
        findings.extend(fixture_findings(all_cases, entries, warn_unused=registry_in_scope))
        _print_findings([f.__dict__ for f in findings])
        errors = sum(1 for f in findings if f.severity == "error")
        warnings = sum(1 for f in findings if f.severity == "warning")
        typer.echo(f"{len(all_cases)} case(s): {errors} error(s), {warnings} warning(s)")
        if errors:
            raise typer.Exit(code=1)
        return
    conn = _connection()
    me = conn.username or ""
    params = {"dataset": slug(_parse(dataset, me))} if dataset else {}
    form: dict[str, str] = {}
    if agent:
        ref = _parse(agent, me)
        definition = _agent_definition(ref)
        _require_own_agent(definition, me, "lint against")
        if server_may_drive(definition):
            params["agent"] = slug(ref)
        else:
            local = _with_secrets(_local_config(), scoring=False)
            try:
                schema = asyncio.run(local_input_schema(conn, local, ref))
            except (httpx.HTTPError, ValueError, ApiRequestError) as exc:
                raise ApiError(f"harness {definition.ref}: {exc}") from exc
            form["input_schema"] = json.dumps(schema)
    files = _yaml_files(path)
    registry = _nearest_registry(path)
    if registry is not None:
        files.append((REGISTRY_FILENAME, registry))
        params["warn_unused_fixtures"] = str(registry.parent == path).lower()
    body = _client().post_files("/api/lint", files, params=params, data=form).json()
    _print_findings(body["findings"])
    typer.echo(f"{body['errors']} error(s), {body['warnings']} warning(s)")
    if body["errors"]:
        raise typer.Exit(code=1)


def _sync_and_report(
    dataset_dir: Path,
    *,
    name: str,
    workspace: str,
    revision: GitRevision,
    subpath: str | None = None,
    fixtures_dir: Path | None = None,
    actor: str | None = None,
) -> None:
    try:
        report = sync_dataset(
            _client(),
            dataset_dir,
            name=name,
            workspace=workspace,
            revision=revision,
            subpath=subpath,
            fixtures_dir=fixtures_dir,
            actor=actor,
        )
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    _print_findings(report["findings"])
    if report["aborted"]:
        typer.secho("sync aborted on lint errors", fg="red")
        raise typer.Exit(code=1)
    typer.echo(
        f"synced {slug(Reference(name, workspace))}: created={report['created']} "
        f"patched={report['patched']} deleted={report['deleted']} "
        f"overlays_applied={report['overlays_applied']}"
    )
    review = report.get("review") or {}
    if review.get("now_unreviewed"):
        typer.echo(
            f"{len(review['now_unreviewed'])} approved case(s) changed and are now unreviewed: "
            + ", ".join(review["now_unreviewed"])
        )
    if review.get("carried"):
        typer.echo(
            f"{len(review['carried'])} case(s) updated and still under review: "
            + ", ".join(f"{c['case_id']} ({c['reviewer']})" for c in review["carried"])
        )


@app.command()
def sync(
    path: str = typer.Argument(
        ..., help="Dataset name under the cases root, or a directory or case file on disk."
    ),
    workspace: str = typer.Option(
        None,
        "--workspace",
        help="Target workspace; defaults to yours. CI passes the shared one, e.g. dev.",
    ),
    version: str | None = typer.Option(
        None, "--version", help="Git sha label; detected from git when omitted."
    ),
    branch: str | None = typer.Option(None, "--branch", help="Detected from git when omitted."),
    dirty: bool | None = typer.Option(
        None, "--dirty/--no-dirty", help="Detected from git when omitted."
    ),
    actor: str | None = typer.Option(
        None, "--actor", help="Record this user as the author of the sync (admin only)."
    ),
):
    """Import a dataset into a workspace. The dataset is the directory under the cases
    root (or the directory itself without an evalkit.yaml); a whole dataset prunes cases no
    longer on disk, a subfolder or file patches just its cases. Uploads case YAML plus the
    nearest fixtures.yaml.

    Example: evalkit sync finance · evalkit sync finance/statement-analysis ·
    evalkit sync datasets/finance --workspace dev --version $GITHUB_SHA
    """
    conn = _connection()
    me = conn.username or ""
    target = workspace or me
    local = _find_local()
    resolved = _resolve(path, local.cases if local else None, me)
    if resolved.dataset_dir is None:
        raise ApiError(f"{path} is not on disk; sync needs a directory or file")
    detected = git_revision(resolved.dataset_dir)
    revision = GitRevision(
        sha=version if version is not None else detected.sha,
        branch=branch if branch is not None else detected.branch,
        dirty=dirty if dirty is not None else detected.dirty,
    )
    _sync_and_report(
        resolved.dataset_dir,
        name=resolved.ref.name,
        workspace=target,
        revision=revision,
        subpath=resolved.subpath,
        fixtures_dir=local.fixtures if local else None,
        actor=actor,
    )


@datasets_app.command("create")
def datasets_create(
    dataset: str = typer.Argument(..., help="finance (yours) or finance@prod."),
):
    """Create an empty suite dataset (writers of the workspace).

    A promotion target is fed by approvals in its source workspace, so its datasets
    start here. Example: evalkit datasets create finance@prod
    """
    ref = _parse(dataset, _connection().username or "")
    _client().post("/api/datasets", json={"name": ref.name, "workspace": ref.workspace})
    typer.echo(f"created {slug(ref)}")


@datasets_app.command("list")
def datasets_list(
    dataset: str | None = typer.Argument(
        None, help="List this dataset's cases instead: finance (yours) or finance@dev."
    ),
    state: CaseState | None = typer.Option(
        None, "--state", help="Only cases in this review state (shared workspaces only)."
    ),
    tag: str | None = typer.Option(None, "--tag", help="Only cases carrying this tag."),
):
    """List datasets with kind and version, or one dataset's cases.

    With a dataset, prints each case's id, review state (shared workspaces only; `-`
    elsewhere), tags and fixture keys.

    Example: evalkit datasets list · evalkit datasets list finance@dev --state approved
    · evalkit datasets list finance --tag statement-analysis
    """
    if dataset is None:
        if state is not None or tag is not None:
            raise ApiError("--state and --tag filter a dataset's cases; pass a dataset")
        for ds in _client().get("/api/datasets").json():
            last = ds.get("last_sync") or {}
            sha = (last.get("detail") or {}).get("sha") or "-"
            typer.echo(f"{ds['ref']:<32} kind={ds['kind']:<10} sha={sha[:12]}")
        return
    dataset_slug = slug(_parse(dataset, _connection().username or ""))
    params = {"tag": tag} if tag is not None else {}
    rows = _client().get(f"/api/datasets/{encode(dataset_slug)}/cases", params=params).json()
    if state is not None and rows and all(row["review"] is None for row in rows):
        raise ApiError(f"{dataset_slug} has no review state; only suites in shared workspaces do")
    shown = [row for row in rows if state is None or (row["review"] or {}).get("state") == state]
    for row in shown:
        review = row["review"] or {}
        fixtures = [f["key"] for f in row["files"]]
        typer.echo(
            f"{row['case_id']:<40} {review.get('state', '-'):<18} "
            f"tags={','.join(row['split_tags']) or '-'} "
            f"fixtures={','.join(fixtures) or '-'}"
        )
    typer.echo(f"{len(shown)} case(s) in {dataset_slug}")


class _Progress:
    def __init__(self) -> None:
        self.exp_id: int | None = None

    def __call__(self, line: str) -> None:
        if line.startswith("experiment "):
            self.exp_id = int(line.split()[1])
        typer.echo(line)


def _warn_dropped(dropped: list[str]) -> None:
    if dropped:
        typer.secho(dropped_cases_text(dropped), fg="yellow")


def _run_on_server(
    dataset_slug: str,
    agent_slug: str,
    selection: Selection,
    repetitions: int,
    model: str | None,
    resume: int | None,
    revision: GitRevision,
    judge: dict | None,
) -> int:
    client = _client()
    if resume:
        exp_id = int(client.post(f"/api/experiments/{resume}/resume").json()["id"])
    else:
        payload = {
            "dataset": dataset_slug,
            "agent": agent_slug,
            "repetitions": repetitions,
            "selection": selection.model_dump(exclude_none=True),
            "model": model,
            "cases": revision.model_dump(exclude_none=True) or None,
            "evalkit_version": version.current(),
            "judge": judge,
        }
        created = client.post("/api/experiments", json=payload).json()
        exp_id = int(created["id"])
        _warn_dropped(created.get("dropped_cases") or [])
    typer.echo(f"experiment {exp_id}: {_experiment_url(exp_id)}")
    last = None
    while True:
        body = client.get(f"/api/experiments/{exp_id}").json()
        if body["status"] != last:
            typer.echo(f"  {body['status']} ({_progress_text(body['progress'])})")
            last = body["status"]
        if body["status"] in {"completed", "failed"}:
            break
        time.sleep(RUN_POLL_SECONDS)
    if body["status"] == "failed":
        typer.secho(f"experiment failed: {body.get('status_error')}", fg="red")
        raise typer.Exit(code=1)
    return exp_id


@app.command()
def run(
    dataset: str = typer.Argument(
        ...,
        help="finance · finance@dev · finance/<split> · finance/<file>.yaml · a path",
    ),
    agent: str = typer.Option(
        ...,
        "--agent",
        help="myagent (yours, your laptop drives it) or ouragent@dev (the server drives it).",
    ),
    runs: int = typer.Option(1, "--runs", help="Repetitions per case."),
    case_id: list[str] = typer.Option(None, "--case-id", help="Run only these case ids."),
    model: str = typer.Option(None, "--model", help="Model override (if the harness accepts it)."),
    resume: int = typer.Option(None, "--resume", help="Resume an experiment by id."),
    judge_model: str | None = typer.Option(
        None, "--judge-model", help="Judge model for this experiment; default: the server's."
    ),
    judge_base_url: str | None = typer.Option(
        None, "--judge-base-url", help="Judge endpoint; its key is credentials.judges[url]."
    ),
    judge_effort: str | None = typer.Option(None, "--judge-effort", help="Judge reasoning effort."),
):
    """Run an experiment. Your own agent runs from this machine; results land on the server.

    Runs every selected case, whatever its review state. Checks every selected
    case's input against the agent's input_schema (or, when it declares none,
    the default: a required string `question`) and refuses the whole experiment
    if any case fails, listing each one. `evalkit lint <dir> --agent <name>`
    shows the same errors before you sync.

    Example: evalkit run finance --agent research-agent ·
    evalkit run finance@dev/statement-analysis --agent ouragent@dev
    """
    conn = _connection()
    me = conn.username or ""
    agent_ref = _parse(agent, me)
    definition = _agent_definition(agent_ref)
    route = driver(definition)
    if route is Driver.SDK:
        raise ApiError(
            f"{definition.ref} is a code-driven agent (no harness_url); "
            "run it with evalkit.sdk.run_experiment, or if it serves the Harness API, "
            f"set its URL: evalkit agents edit {definition.ref} --harness-url URL"
        )
    if route is Driver.CLI and definition.workspace != me:
        raise ApiError(
            f"{definition.ref} belongs to {definition.workspace}'s workspace; "
            "only its owner can run it"
        )
    local = _find_local()
    if local is not None and route is Driver.CLI:
        local = _with_secrets(local, scoring=True)
        _require_agent_key(local, conn, definition)
    resolved = _resolve(dataset, local.cases if local else None, me)
    selection = Selection(case_ids=list(case_id)) if case_id else resolved.selection
    revision = git_revision(resolved.dataset_dir) if resolved.dataset_dir else GitRevision()
    if resolved.dataset_dir is not None and resume is None:
        _sync_and_report(
            resolved.dataset_dir,
            name=resolved.ref.name,
            workspace=me,
            revision=revision,
            fixtures_dir=local.fixtures if local else None,
        )
    dataset_slug = slug(resolved.ref)
    judge = _judge_override(judge_model, judge_base_url, judge_effort)
    if route is Driver.SERVER:
        exp_id = _run_on_server(
            dataset_slug,
            slug(agent_ref),
            selection,
            runs,
            model,
            resume,
            revision,
            judge,
        )
    else:
        if local is None:
            raise ApiError("no evalkit.yaml found above the current directory")
        progress = _Progress()
        try:
            exp_id = asyncio.run(
                run_local(
                    conn,
                    local,
                    agent=agent_ref,
                    dataset_slug=dataset_slug,
                    selection=selection,
                    repetitions=runs,
                    model=model,
                    resume=resume,
                    revision=revision,
                    progress=progress,
                    judge=JudgeSettings.model_validate(judge) if judge else None,
                )
            )
        except KeyboardInterrupt:
            if progress.exp_id is not None:
                asyncio.run(mark_interrupted(conn, progress.exp_id, me))
            typer.secho("interrupted", fg="red")
            raise typer.Exit(code=130) from None
        except ScoringFailed as exc:
            raise ApiError(f"{exc}\nfinish it with: evalkit evaluate {exc.exp_id}") from exc
        except (VersionMismatch, ApiRequestError, ValueError, KeyError) as exc:
            raise ApiError(str(exc)) from exc
    _render_summary(_client().get(f"/api/experiments/{exp_id}/scorecard").json())


def _judge_override(model: str | None, base_url: str | None, effort: str | None) -> dict | None:
    changes = {
        key: value
        for key, value in {
            "model": model,
            "base_url": base_url,
            "reasoning_effort": effort,
        }.items()
        if value is not None
    }
    if not changes:
        return None
    return {**_client().get("/api/judge-default").json(), **changes}


def _require_agent_key(
    local: LocalConfig, conn: connection.Connection, agent: AgentDefinition
) -> None:
    try:
        local_credentials(local, conn).key_for("agent", agent.ref)
    except MissingCredential as exc:
        raise ApiError(str(exc)) from exc


def _agent_definition(ref: Reference) -> AgentDefinition:
    return definition_of(_client().get(f"/api/agents/{encode(explicit(ref))}").json())


@app.command()
def evaluate(
    experiment_id: int,
    refetch_trajectories: bool = typer.Option(
        False,
        "--refetch-trajectories",
        help="Poll the collector again for runs whose trajectory was partial or unavailable, "
        "then re-score them.",
    ),
):
    """Re-score an experiment's recorded runs from this machine (idempotent).

    Scores already recorded are kept; evaluators that errored run again.

    Refuses experiments that used Python evaluators; re-score those with
    evalkit.sdk.evaluate_experiment from the script that defines them.

    Example: evalkit evaluate <experiment-id> --refetch-trajectories
    """
    local = _with_secrets(_local_config(), scoring=True)
    conn = _connection()
    body = _client().get(f"/api/experiments/{experiment_id}").json()
    python_evaluators = body["config"]["snapshot"].get("code_evaluators") or []
    if python_evaluators:
        raise ApiError(
            f"experiment {experiment_id} used python evaluators "
            f"{', '.join(python_evaluators)}; re-score it with evalkit.sdk.evaluate_experiment "
            "from the script that defines them"
        )
    try:
        asyncio.run(
            evaluate_local(
                conn,
                local,
                experiment_id,
                progress=typer.echo,
                refetch_trajectories=refetch_trajectories,
            )
        )
    except ScoringFailed as exc:
        raise ApiError(f"{exc}\nfinish it with: evalkit evaluate {experiment_id}") from exc
    except (VersionMismatch, ApiRequestError, ValueError, KeyError) as exc:
        raise ApiError(str(exc)) from exc
    typer.echo(f"evaluated {experiment_id}")


def _progress_text(progress: dict) -> str:
    total = progress["total"]
    return f"{progress['ran']}/{total} ran, {progress['evaluated']}/{total} evaluated"


def _experiment_url(exp_id: int) -> str:
    return f"{_connection().url.rstrip('/')}/experiments/{exp_id}"


def _lease_text(body: dict) -> str:
    if body["status"] not in {"running", "evaluating"} or not body.get("lease_holder"):
        return ""
    expires = datetime.fromisoformat(body["lease_expires_at"])
    left = int((expires - datetime.now(UTC)).total_seconds())
    if left < 0:
        return f"\n  held by {body['lease_holder']} (lease expired {-left}s ago; runner stopped?)"
    return f"\n  held by {body['lease_holder']} (lease expires in {left}s)"


@app.command()
def status(experiment_id: int):
    """Show an experiment's status, progress and, while it runs, who holds it.

    Example: evalkit status 42
    """
    body = _client().get(f"/api/experiments/{experiment_id}").json()
    typer.echo(
        f"experiment {body['id']}  #{body['number']} of {body['dataset']}  agent={body['agent']}\n"
        f"  status={body['status']} {_progress_text(body['progress'])}{_lease_text(body)}"
    )
    if body.get("status_error"):
        typer.secho(f"  error: {body['status_error']}", fg="red")


def _render_summary(report: dict) -> None:
    for line in summary_lines(report):
        typer.echo(line)


@app.command()
def summary(
    experiment_id: int,
    baseline: int = typer.Option(None, "--baseline", help="Baseline experiment id for deltas."),
):
    """Render an experiment's scorecard as a console table.

    Example: evalkit summary <experiment-id> --baseline <older-id>
    """
    params = {"baseline_id": baseline} if baseline else {}
    _render_summary(
        _client().get(f"/api/experiments/{experiment_id}/scorecard", params=params).json()
    )


def _require_own_agent(definition: AgentDefinition, me: str, action: str) -> None:
    if not definition.workspace_is_shared and definition.workspace != me:
        raise ApiError(
            f"{definition.ref} belongs to {definition.workspace}'s workspace; "
            f"only its owner can {action} it"
        )


@harness_app.command("check")
def harness_check(
    agent: str = typer.Argument(
        ..., help="myagent (yours, your laptop drives it) or ouragent@dev (the server drives it)."
    ),
):
    """Exercise a harness's Harness API and print a per-capability table.

    Example: evalkit harness check myagent · evalkit harness check ouragent@dev
    """
    conn = _connection()
    ref = _parse(agent, conn.username or "")
    definition = _agent_definition(ref)
    _require_own_agent(definition, conn.username or "", "check")
    if definition.archived:
        raise ApiError(f"agent {definition.ref} is archived")
    if driver(definition) is Driver.SDK:
        raise ApiError(
            f"{definition.ref} is a code-driven agent (no harness_url); "
            "use evalkit traces check AGENT SESSION_ID"
        )
    if server_may_drive(definition):
        checks = _client().post(f"/api/agents/{encode(slug(ref))}/check").json()["checks"]
    else:
        local = _with_secrets(_local_config(), scoring=False)
        try:
            checks = asyncio.run(check_local(conn, local, ref))
        except (ValueError, KeyError, ApiRequestError) as exc:
            raise ApiError(str(exc)) from exc
    failed = False
    for capability, entry in checks.items():
        marker = {"pass": "✓", "warn": "!", "fail": "✗", "skipped": "-"}[entry["status"]]
        typer.echo(f"  {marker} {capability:<14} {entry['detail']}")
        failed = failed or entry["status"] == "fail"
    if failed:
        raise typer.Exit(code=1)


@users_app.command("add")
def users_add(username: str, password: str = typer.Option(..., prompt=True, hide_input=True)):
    """Create a user (admin only). Example: evalkit users add alice"""
    _client().post("/api/users", json={"username": username, "password": password})
    typer.echo(f"created {username}")


@users_app.command("archive")
def users_archive(username: str):
    """Archive a user: revoke their login tokens, block login, archive their workspace.

    Agent tokens they minted keep working: they belong to the agent.

    Users are never deleted, so their experiments keep an owner and the name
    stays reserved. Admin only. Example: evalkit users archive alice
    """
    _client().post(f"/api/users/{username}/archive")
    typer.echo(f"archived {username}")


@users_app.command("restore")
def users_restore(username: str):
    """Let an archived user log in again (admin only). Example: evalkit users restore alice

    Their personal workspace is restored too when it was archived with them.
    """
    _client().post(f"/api/users/{username}/restore")
    typer.echo(f"restored {username}")


@users_app.command("set-password")
def users_set_password(
    username: str,
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
):
    """Set a user's password and sign them out everywhere (admin only).

    Example: evalkit users set-password alice
    """
    _client().post(f"/api/users/{username}/password", json={"password": password})
    typer.echo(f"password set for {username}")


@users_app.command("promote")
def users_promote(username: str):
    """Make a user an admin (admin only). Example: evalkit users promote alice"""
    _client().post(f"/api/users/{username}/promote")
    typer.echo(f"{username} is an admin")


@users_app.command("demote")
def users_demote(username: str):
    """Remove a user's admin flag; the last admin stays. Example: evalkit users demote alice"""
    _client().post(f"/api/users/{username}/demote")
    typer.echo(f"{username} is no longer an admin")


@users_app.command("list")
def users_list():
    """List users (admin only). Example: evalkit users list"""
    for user in _client().get("/api/users").json():
        flags = " ".join(
            flag for flag, on in (("ADMIN", user["is_admin"]), ("ARCHIVED", user["archived"])) if on
        )
        typer.echo(f"{user['username']:<24} {flags:<16} created {user['created_at']}")


DOCS_DIR = "evalkit-docs"
INSTALL_LEAVES_OUT = shutil.ignore_patterns(".env", "*.whl", "__pycache__", "*.pyc")


@skills_app.command("install")
def skills_install(
    target: Path = typer.Option(
        None,
        "--target",
        help="Skills dir; default .claude/skills/ if .claude/ exists, else .agents/skills/",
    ),
):
    """Copy the bundled skills, and the user docs they link to, into this repo's agent-skills
    directory.

    The docs go to evalkit-docs/ beside the skills. A directory an earlier install put there
    that this version no longer ships is removed; other directories are left alone.
    `evalkit update` refreshes every directory installed this way.

    Example: evalkit skills install
    """
    root = skills_root()
    skills = sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())
    if not skills:
        raise ApiError(f"no skills found under {root}")
    if target is None:
        target = Path(".claude/skills") if Path(".claude").is_dir() else Path(".agents/skills")
    target.mkdir(parents=True, exist_ok=True)
    sources = {skill.name: skill for skill in skills} | {DOCS_DIR: docs_root()}
    manifest = target / INSTALL_MANIFEST
    previous = manifest.read_text().split() if manifest.is_file() else []
    for name in previous:
        if name not in sources and (target / name).is_dir():
            shutil.rmtree(target / name)
            typer.echo(f"removed {name} (no longer shipped)")
    for name, source in sources.items():
        destination = target / name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=INSTALL_LEAVES_OUT)
        typer.echo(f"installed {name} → {destination}")
    manifest.write_text("".join(f"{name}\n" for name in sources))
    record_skill_target(target)


@contracts_app.command("export")
def contracts_export(
    target: Path = typer.Option(
        Path("contracts"), "--target", help="Directory to write the contract files into."
    ),
):
    """Write the bundled API contracts (OpenAPI + accepted ATIF versions) to a directory.

    Exports harness-api-v1.json, custom-evaluator-api-v1.json and
    atif-accepted.json, matching the installed evalkit version.

    Example: evalkit contracts export --target ./contracts
    """
    root = contracts_root()
    files = sorted(p for p in root.iterdir() if p.suffix == ".json")
    if not files:
        raise ApiError(f"no contract files found under {root}")
    target.mkdir(parents=True, exist_ok=True)
    for file in files:
        shutil.copyfile(file, target / file.name)
        typer.echo(f"exported {file.name} → {target / file.name}")


@app.command("init-server")
def init_server_command(
    directory: Path = typer.Option(
        init_server.DEFAULT_DIR, "--dir", help="Directory for the try kit, config.yaml and .env."
    ),
    database_url: str | None = typer.Option(
        None, "--database-url", help="Postgres URL; the database is created when missing."
    ),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_api_key: str | None = typer.Option(None, "--llm-api-key"),
    admin_password: str | None = typer.Option(None, "--admin-password"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Never prompt; use flags, env, defaults."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config.yaml."),
    kit_only: bool = typer.Option(
        False, "--kit-only", help="Only copy the try kit; no database, config.yaml or .env."
    ),
):
    """Set up an evalkit server on this machine, against a Postgres you already run.

    Copies the try kit (sample agent, cases, run.py) into --dir, creates the database
    when missing, writes config.yaml (blobs under <dir>/data) and .env (your LLM and the
    admin password), and migrates the database. Values come from flags, then env
    (EVALKIT_DATABASE_URL, LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, EVALKIT_ADMIN_PASSWORD),
    then prompts.

    Example: evalkit init-server · evalkit init-server --kit-only --dir ./evalkit-try
    """
    if kit_only:
        init_server.copy_kit(directory)
        typer.echo(f"copied the try kit to {directory}")
        return
    if server_commands is None:
        raise ApiError(
            "`evalkit init-server` needs the server extra — pip install 'evalkit-py[server]'"
        )
    if (directory / "config.yaml").exists() and not force:
        raise ApiError(f"{directory / 'config.yaml'} exists; pass --force to overwrite it")
    flags = {
        "database_url": database_url,
        "llm_base_url": llm_base_url,
        "llm_model": llm_model,
        "llm_api_key": llm_api_key,
        "admin_password": admin_password,
    }
    answers = init_server.resolve(flags, yes=yes)
    typer.echo(
        f"connecting to {answers.database_url} (is Postgres running? brew services start "
        "postgresql@17, sudo systemctl start postgresql, or Postgres.app)"
    )
    created = server_commands.init_database(answers.database_url)
    typer.echo(
        "created the database and migrated it"
        if created
        else "the database already existed; migrated it"
    )
    init_server.copy_kit(directory)
    init_server.write_server_files(directory, answers)
    typer.echo(f"set up {directory}: config.yaml, .env and the try kit")
    typer.echo("next:")
    typer.echo(f"  cd {directory} && evalkit serve")
    typer.echo("  then open http://localhost:8020 and follow Try it locally from step 1")


@app.command()
def serve(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml."),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8020, "--port"),
):
    """Start the evalkit server (API + UI).

    Example: evalkit serve --config /etc/evalkit/config.yaml
    """
    if server_commands is None:
        raise ApiError("`evalkit serve` needs the server extra — pip install 'evalkit[server]'")
    server_commands.serve(_server_config(config), host, port)


@db_app.command("upgrade")
def db_upgrade(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml."),
    revision: str = typer.Option("head", "--revision", help="Stop at this revision, e.g. 0006."),
):
    """Run schema migrations against the configured database.

    Example: evalkit db upgrade --config /etc/evalkit/config.yaml --revision 0006
    """
    if server_commands is None:
        raise ApiError(
            "`evalkit db upgrade` needs the server extra — pip install 'evalkit[server]'"
        )
    server_commands.db_upgrade(_server_config(config), revision)
    typer.echo(f"migrations applied up to {revision}")


@trace_sources_app.command("rekey")
def trace_sources_rekey(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Path to config.yaml."),
    old_key_env: str = typer.Option(..., "--old-key-env", help="Env var with the old key."),
    new_key_env: str = typer.Option(..., "--new-key-env", help="Env var with the new key."),
):
    """Re-encrypt every trace source key after an encryption_key change (server-side).

    Example: evalkit trace-sources rekey --config config.yaml --old-key-env OLD --new-key-env NEW
    """
    if server_commands is None:
        raise ApiError(
            "`trace-sources rekey` needs the server extra — pip install 'evalkit[server]'"
        )
    missing = [name for name in (old_key_env, new_key_env) if name not in os.environ]
    if missing:
        raise ApiError(f"set {', '.join(missing)} first")
    count = asyncio.run(
        server_commands.rekey_trace_sources(
            _server_config(config), os.environ[old_key_env], os.environ[new_key_env]
        )
    )
    typer.echo(f"re-encrypted {count} trace source key(s)")


if __name__ == "__main__":
    app()


def _selection(tag: str | None, case_id: list[str] | None) -> dict:
    if case_id:
        return {"case_ids": list(case_id)}
    if tag:
        return {"tag": tag}
    return {"all": True}


def _dataset_ref(dataset: str) -> str:
    return encode(slug(_parse(dataset, _connection().username or "")))


def _case_path(dataset: str, case_id: str) -> str:
    return f"/api/datasets/{_dataset_ref(dataset)}/cases/{case_id}"


def _fmt_comment(comment: dict, *, indent: int) -> str:
    flag = " [resolved]" if comment["resolved"] else ""
    pad = " " * indent
    head = f"{pad}#{comment['id']} {comment['author']} · {comment['created_at']}{flag}"
    return f"{head}\n{pad}{comment['body']}"


def _post_decision(dataset: str, case_id: str, action: str, comment: str | None) -> None:
    body = {"action": action, "comment": comment}
    result = _client().post(f"{_case_path(dataset, case_id)}/review", json=body).json()
    typer.echo(f"{case_id}: {result['state']}")


@review_app.command("request")
def review_request(
    dataset: str = typer.Argument(..., help="finance (yours) or finance@dev."),
    reviewer: str = typer.Option(..., "--reviewer", help="Reviewer username."),
    tag: str = typer.Option(None, "--tag", help="Only cases carrying this tag."),
    case_id: list[str] = typer.Option(None, "--case-id", help="Only these case ids."),
):
    """Ask a reviewer to look at cases (all cases when no selector is given).

    Example: evalkit review request finance --case-id mf-industry-07 --reviewer sme@client
    """
    body = {"selection": _selection(tag, case_id), "reviewer": reviewer}
    requested = (
        _client().post(f"/api/datasets/{_dataset_ref(dataset)}/review-requests", json=body).json()
    )
    typer.echo(f"requested review from {reviewer} on {len(requested['requested'])} case(s)")


@review_app.command("show")
def review_show(
    dataset: str = typer.Argument(..., help="finance (yours) or finance@dev."),
    case_id: str = typer.Argument(None, help="Case id; omit to list every case's state."),
):
    """Show review state for a dataset, or state, changelog and comments for one case.

    Example: evalkit review show finance us10k-conv-04
    """
    client = _client()
    rows = client.get(f"/api/datasets/{_dataset_ref(dataset)}/cases").json()
    if case_id is None:
        for ex in rows:
            review = ex.get("review") or {}
            typer.echo(
                f"{ex['case_id']:<40} {review.get('state', '-'):<18} "
                f"{review.get('requested_to') or ''}"
            )
        return
    match = next((ex for ex in rows if ex["case_id"] == case_id), None)
    if match is None:
        raise ApiError(f"unknown case: {case_id}")
    review = match.get("review") or {}
    typer.echo(f"state: {review.get('state', '-')}")
    if review.get("requested_to"):
        typer.echo(f"requested to: {review['requested_to']}")
    typer.echo("changelog:")
    for event in client.get(f"{_case_path(dataset, case_id)}/events").json()["events"]:
        target = f" → {event['target']}" if event.get("target") else ""
        typer.echo(
            f"  {event['created_at']}  {event['kind']:<18} {event['actor']}{target}  "
            f"[{event['content_hash']}]"
        )
    typer.echo("comments:")
    for thread in client.get(f"{_case_path(dataset, case_id)}/comments").json()["threads"]:
        typer.echo(_fmt_comment(thread, indent=2))
        for reply in thread["replies"]:
            typer.echo(_fmt_comment(reply, indent=6))


@review_app.command("approve")
def review_approve(
    dataset: str = typer.Argument(..., help="finance (yours) or finance@dev."),
    case_id: str = typer.Argument(..., help="Case id."),
    comment: str = typer.Option(None, "--comment", help="Optional note."),
):
    """Approve a case (reviewers only).

    Example: evalkit review approve finance us10k-conv-04
    """
    _post_decision(dataset, case_id, "approve", comment)


@review_app.command("request-changes")
def review_request_changes(
    dataset: str = typer.Argument(..., help="finance (yours) or finance@dev."),
    case_id: str = typer.Argument(..., help="Case id."),
    comment: str = typer.Option(..., "--comment", help="What needs to change."),
):
    """Send a case back to its author with a comment (reviewers only).

    Example: evalkit review request-changes finance us10k-conv-04 --comment "anchor the date"
    """
    _post_decision(dataset, case_id, "request_changes", comment)


@review_app.command("comment")
def review_comment(
    dataset: str = typer.Argument(..., help="finance (yours) or finance@dev."),
    case_id: str = typer.Argument(..., help="Case id."),
    text: str = typer.Argument(..., help="Comment body."),
    reply_to: int = typer.Option(None, "--reply-to", help="Comment id to reply to."),
):
    """Comment on a case, or reply to a comment.

    Example: evalkit review comment finance us10k-conv-04 "which fiscal year?"
    """
    body = {"body": text, "parent_id": reply_to}
    stored = _client().post(f"{_case_path(dataset, case_id)}/comments", json=body).json()
    typer.echo(f"comment {stored['id']} on {case_id}")


@review_app.command("resolve")
def review_resolve(
    dataset: str = typer.Argument(..., help="finance (yours) or finance@dev."),
    case_id: str = typer.Argument(..., help="Case id."),
    comment_id: int = typer.Argument(..., help="Comment id."),
    undo: bool = typer.Option(False, "--undo", help="Mark the comment unresolved again."),
):
    """Resolve (or with --undo, unresolve) a comment.

    Example: evalkit review resolve finance us10k-conv-04 12
    """
    result = (
        _client()
        .patch(f"{_case_path(dataset, case_id)}/comments/{comment_id}", json={"resolved": not undo})
        .json()
    )
    typer.echo(f"comment {result['id']} {'resolved' if result['resolved'] else 'unresolved'}")


@review_app.command("queue")
def review_queue(
    reviewer: str = typer.Option(None, "--reviewer", help="Whose queue; defaults to you."),
    everyone: bool = typer.Option(False, "--all", help="Every case under review."),
    changes_requested: bool = typer.Option(
        False, "--changes-requested", help="Cases sent back to their authors instead."
    ),
):
    """List cases waiting for review, or with --changes-requested, cases awaiting an edit.

    Example: evalkit review queue
    """
    client = _client()
    params: dict = {}
    if changes_requested:
        params["state"] = "changes_requested"
    elif not everyone:
        params["reviewer"] = reviewer or client.get("/api/auth/me").json()["username"]
    items = client.get("/api/review/queue", params=params).json()["items"]
    if not items:
        typer.echo("nothing waiting")
        return
    for item in items:
        typer.echo(
            f"{item['dataset']:<20} {item['case_id']:<40} by {item['requested_by']:<16} "
            f"to {item['requested_to'] or '-':<16} {item['input'][:60]}"
        )
