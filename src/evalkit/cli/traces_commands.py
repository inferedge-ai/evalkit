from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer

from evalkit.cli.client import ApiClient, ApiError
from evalkit.client.api import encode
from evalkit.core.traces.check import render_check
from evalkit.core.traces.presets import list_preset_names, read_preset

traces_app = typer.Typer(
    no_args_is_help=True, help="Check, map and import OpenTelemetry sessions for an agent."
)
mapping_app = typer.Typer(no_args_is_help=True, help="Manage an agent's span mapping.")
traces_app.add_typer(mapping_app, name="mapping")

DUMPS = ("spans.json", "esf.json", "trajectory.json")
FOLLOW_SECONDS = 2
ACTIVE_IMPORT = ("running", "stopping")


MAPPING_HELP = (
    "A preset name (evalkit traces mapping presets) or a mapping JSON file; a file wins "
    "when both match."
)


def read_mapping(value: str) -> str:
    if Path(value).is_file():
        return Path(value).read_text()
    names = list_preset_names()
    if value in names:
        return read_preset(value)
    raise ApiError(f"{value} is neither a file nor a mapping preset; presets: {', '.join(names)}")


def load_mapping(value: str) -> dict:
    return json.loads(read_mapping(value))


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def import_line(view: dict) -> str:
    since, until = utc(view["since"]), utc(view["until"])
    reached = utc(view["resume_from"])
    done = 1.0 if view["status"] == "done" else (reached - since) / (until - since)
    return (
        f"{view['id']:<4} {view['status']:<8} {reached:%Y-%m-%d %H:%M}  {round(100 * done):>3}%  "
        f"{view['traces']} traces  {view['spans']} spans"
    )


def register(
    app: typer.Typer, client_factory: Callable[[], ApiClient], agent_slug: Callable[[str], str]
) -> None:
    app.add_typer(traces_app, name="traces")

    @traces_app.command("check")
    def traces_check(
        agent: str = typer.Argument(..., help="myagent (yours) or ouragent@dev."),
        session_id: str = typer.Argument(
            ..., help="The session id (an SDK run id or a harness run id)."
        ),
        dump: Path = typer.Option(
            None,
            "--dump",
            help="Write the raw spans, ESF or trajectory to this path; the file name picks "
            "which (ends in spans.json, esf.json or trajectory.json, e.g. /tmp/run7-spans.json). "
            "esf.json omits the input messages of LLM calls after the first under each root span "
            "and in each subagent; the raw "
            "values are in spans.json.",
        ),
        mapping: str = typer.Option("active", "--mapping", help="active or draft."),
    ):
        """Print a session's settle state, span kinds, tokens, unmapped attributes and ATIF summary.

        Exit 1 when the session has no root span. Example: evalkit traces check research-agent 7f3c…
        """
        client = client_factory()
        slug = agent_slug(agent)
        base = f"/api/agents/{encode(slug)}/sessions/{encode(session_id)}"
        params = {"mapping": mapping, "esf": "true"}
        session = client.get(f"{base}/trajectory", params=params).json()
        if dump is not None:
            if dump.name.endswith("spans.json"):
                payload = client.get(f"{base}/spans").json()
            elif dump.name.endswith("esf.json"):
                payload = session.get("esf")
            elif dump.name.endswith("trajectory.json"):
                payload = session.get("trajectory")
            else:
                raise ApiError(f"--dump file name must end in one of {', '.join(DUMPS)}")
            dump.parent.mkdir(parents=True, exist_ok=True)
            dump.write_text(json.dumps(payload, indent=2))
            typer.echo(f"wrote {dump}")
        lines, ok = render_check(session)
        for line in lines:
            typer.echo(line)
        if not ok:
            raise typer.Exit(code=1)

    def print_session_check(client: ApiClient, slug: str, session_id: str) -> None:
        settle = int(client.get("/api/meta").json().get("settle_seconds") or 0)
        time.sleep(settle)
        path = f"/api/agents/{encode(slug)}/sessions/{encode(session_id)}/trajectory"
        session = client.get(path, params={"esf": "true"}).json()
        for line in render_check(session)[0]:
            typer.echo(line)

    def follow(client: ApiClient, agent: str, slug: str, import_id: int) -> None:
        last = None
        while True:
            view = client.get(f"/api/agents/{encode(slug)}/imports/{import_id}").json()
            line = import_line(view)
            if line != last:
                typer.echo(line)
                last = line
            if view["status"] == "done":
                if view["sample_session"]:
                    typer.echo(f"check of session {view['sample_session']}:")
                    print_session_check(client, slug, view["sample_session"])
                else:
                    typer.echo("no sessions were imported")
                return
            if view["status"] not in ACTIVE_IMPORT:
                typer.echo(f"{view['status']}: {view['error'] or 'stopped by a user'}")
                typer.echo(f"to continue: evalkit traces imports {agent} {import_id} --continue")
                raise typer.Exit(code=1)
            time.sleep(FOLLOW_SECONDS)

    @traces_app.command("import")
    def traces_import(
        agent: str,
        session_id: str | None = typer.Argument(
            None, help="One session to import. Leave out and pass --since for a time range."
        ),
        source: str | None = typer.Option(
            None,
            "--source",
            help="A trace source name (evalkit trace-sources list). Default: the agent's.",
        ),
        project: str | None = typer.Option(
            None, "--project", help="The project name in the trace source. Default: the agent's."
        ),
        since: str | None = typer.Option(
            None,
            "--since",
            help="Import every trace that started at or after this time (ISO 8601; UTC unless "
            "it carries an offset), as a server job.",
        ),
        until: str | None = typer.Option(
            None, "--until", help="End of the range, exclusive (ISO 8601). Default: now."
        ),
    ):
        """Import one session, or every trace in a time range, from a trace source.

        A session import prints its check summary. A range import runs on the server; the CLI
        follows its progress and Ctrl-C only stops following.

        Examples: evalkit traces import research-agent abc --source phoenix --project ra;
        evalkit traces import research-agent --source phoenix --project ra --since 2026-10-01
        """
        if (session_id is None) == (since is None):
            raise typer.BadParameter("give either SESSION_ID or --since, not both")
        if until is not None and since is None:
            raise typer.BadParameter("--until needs --since")
        client = client_factory()
        slug = agent_slug(agent)
        target = {k: v for k, v in (("source", source), ("project", project)) if v is not None}
        if session_id is not None:
            body = client.post(
                f"/api/agents/{encode(slug)}/sessions/import",
                json={**target, "session_id": session_id},
            ).json()
            typer.echo(f"imported {body['imported']} spans")
            print_session_check(client, slug, session_id)
            return
        assert since is not None
        window = {
            "since": utc(since).isoformat(),
            "until": (utc(until) if until else datetime.now(UTC)).isoformat(),
        }
        view = client.post(
            f"/api/agents/{encode(slug)}/imports",
            json={**target, **window},
        ).json()
        typer.echo(f"import {view['id']} started: {view['since']} → {view['until']}")
        follow(client, agent, slug, view["id"])

    @traces_app.command("imports")
    def traces_imports(
        agent: str,
        import_id: int | None = typer.Argument(None, help="One import to show or follow."),
        stop: bool = typer.Option(False, "--stop", help="Stop a running import."),
        resume: bool = typer.Option(
            False, "--continue", help="Continue a failed or stopped import from where it got to."
        ),
    ):
        """List an agent's range imports, or show, follow, stop or continue one.

        Examples: evalkit traces imports research-agent;
        evalkit traces imports research-agent 12 --continue
        """
        if stop and resume:
            raise typer.BadParameter("give --stop or --continue, not both")
        if import_id is None and (stop or resume):
            raise typer.BadParameter("--stop and --continue need an import id")
        client = client_factory()
        slug = agent_slug(agent)
        base = f"/api/agents/{encode(slug)}/imports"
        if import_id is None:
            for view in client.get(base).json():
                typer.echo(
                    f"{import_line(view)}  {view['since']} → {view['until']}  "
                    f"{view['source']}/{view['project']}  by {view['created_by']}"
                )
            return
        if stop:
            typer.echo(import_line(client.post(f"{base}/{import_id}/stop").json()))
            return
        if resume:
            client.post(f"{base}/{import_id}/continue")
        follow(client, agent, slug, import_id)

    @mapping_app.command("push")
    def mapping_push(
        agent: str,
        mapping: str = typer.Argument(..., metavar="MAPPING", help=MAPPING_HELP),
    ):
        """Upload a mapping document as the agent's draft. Activate it with mapping activate.

        Example: evalkit traces mapping push research-agent evalkit/span-mapping.json
        """
        document = load_mapping(mapping)
        body = (
            client_factory()
            .put(f"/api/agents/{encode(agent_slug(agent))}/mappings/draft", json=document)
            .json()
        )
        if body["unchanged"]:
            typer.echo(f"draft v{body['version']} for {agent} is unchanged")
            return
        typer.echo(f"draft v{body['version']} for {agent}")

    @mapping_app.command("presets")
    def mapping_presets(
        name: str | None = typer.Argument(
            None, help="Print this preset's JSON. Leave out to list the preset names."
        ),
    ):
        """List the mapping presets that ship with evalkit, or print one as JSON to save and edit.

        Examples: evalkit traces mapping presets;
        evalkit traces mapping presets langchain > evalkit/span-mapping.json
        """
        names = list_preset_names()
        if name is None:
            for preset in names:
                typer.echo(preset)
            return
        if name not in names:
            raise ApiError(f"{name} is not a mapping preset; presets: {', '.join(names)}")
        typer.echo(read_preset(name), nl=False)

    @mapping_app.command("activate")
    def mapping_activate(
        agent: str,
        version: int = typer.Argument(..., help="Version to activate, as push printed it."),
    ):
        """Make a mapping version the active one, which every later run uses.

        Example: evalkit traces mapping activate research-agent 3
        """
        body = (
            client_factory()
            .post(f"/api/agents/{encode(agent_slug(agent))}/mappings/{version}/activate")
            .json()
        )
        replaced = f", replacing v{body['replaced_version']}" if body["replaced_version"] else ""
        typer.echo(f"active v{body['version']} for {agent}{replaced}")

    @mapping_app.command("list")
    def mapping_list(agent: str):
        """List an agent's mapping versions. Example: evalkit traces mapping list research-agent"""
        rows = client_factory().get(f"/api/agents/{encode(agent_slug(agent))}/mappings").json()
        for row in rows:
            typer.echo(
                f"v{row['version']:<4} {row['status']:<8} by {row['created_by'] or '-':<16} "
                f"{row['created_at']}"
            )
