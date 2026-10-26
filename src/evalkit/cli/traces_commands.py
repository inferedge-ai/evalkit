from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import typer

from evalkit.cli.client import ApiClient, ApiError
from evalkit.client.api import encode

traces_app = typer.Typer(
    no_args_is_help=True, help="Check, map and import OpenTelemetry sessions for an agent."
)
mapping_app = typer.Typer(no_args_is_help=True, help="Manage an agent's span mapping.")
traces_app.add_typer(mapping_app, name="mapping")

MARK = {"pass": "✓", "warn": "!", "fail": "✗"}
DUMPS = ("spans.json", "esf.json", "trajectory.json")
FOLLOW_SECONDS = 2
ACTIVE_IMPORT = ("running", "stopping")


def _line(status: str, label: str, detail: str) -> str:
    return f"  {MARK[status]} {label:<14} {detail}"


def _trajectories(trajectory: dict) -> list[dict]:
    out = [trajectory] if trajectory else []
    for sub in trajectory.get("subagent_trajectories") or []:
        out.extend(_trajectories(sub))
    return out


def render_check(session: dict) -> tuple[list[str], bool]:
    esf = session.get("esf") or []
    traces = {s["trace_id"] for s in esf}
    plural = "s" if len(traces) != 1 else ""
    settled = "settled" if session["settled"] else "not settled yet"
    lines = [
        _line(
            "pass" if session["settled"] else "warn",
            "session",
            f"{session['span_count']} spans in {len(traces)} trace{plural}, {settled}",
        )
    ]
    root = next((s for s in esf if s["parent_id"] is None), None)
    if root is None:
        lines.append(_line("fail", "root", "no root span (parent_span_id null) in this session"))
    else:
        ended = "ended" if root["ended_at"] else "not ended"
        lines.append(_line("pass", "root", f'"{root["name"]}" ({root["kind"]}), {ended}'))
    version = session.get("mapping_version")
    lines.append(
        _line("pass", "mapping", f"v{version}")
        if version
        else _line("warn", "mapping", "no active mapping; the agent's spans are unmapped")
    )
    llms = [s for s in esf if s["kind"] == "llm"]
    if llms:
        models = sorted({s["model"] for s in llms if s.get("model")})
        bare = sum(1 for s in llms if not s.get("input_messages") and not s.get("output_messages"))
        keys = ("prompt_tokens", "completion_tokens", "cached_tokens")
        totals = {k: sum(s.get(k) or 0 for s in llms) for k in keys}
        detail = (
            f"{len(llms)} spans, model {', '.join(models) or 'unknown'}, tokens "
            f"in={totals['prompt_tokens']} out={totals['completion_tokens']} "
            f"cached={totals['cached_tokens']}"
        )
        if bare:
            detail += f", {bare} without messages (is message content capture on?)"
        no_tokens = not totals["prompt_tokens"] and not totals["completion_tokens"]
        if no_tokens:
            detail += ", no token counts (check the tokens rule)"
        lines.append(_line("warn" if bare or no_tokens else "pass", "llm", detail))
    else:
        lines.append(_line("warn", "llm", "no llm spans; check the kind rule"))
    tools = [s for s in esf if s["kind"] == "tool"]
    trajectory = session.get("trajectory") or {}
    calls = [c for step in trajectory.get("steps") or [] for c in step.get("tool_calls") or []]
    every = _trajectories(trajectory)
    call_ids = {
        c["tool_call_id"]
        for t in every
        for s in t.get("steps") or []
        for c in s.get("tool_calls") or []
    }
    matched = len({s.get("tool_call_id") for s in tools if s.get("tool_call_id")} & call_ids)
    unplaced = [n for t in every for n in (t.get("extra") or {}).get("unplaced_tool_spans") or []]
    detail = f"{len(tools)} spans, {matched} matched to calls by id"
    if unplaced:
        detail += f", {len(unplaced)} in no step ({', '.join(unplaced[:2])})"
    lines.append(_line("pass" if tools and not unplaced else "warn", "tools", detail))
    unmapped = session.get("unmapped_attributes") or []
    if unmapped:
        more = f" (+{len(unmapped) - 2})" if len(unmapped) > 2 else ""
        lines.append(_line("warn", "unmapped", ", ".join(unmapped[:2]) + more))
    else:
        lines.append(_line("pass", "unmapped", "every attribute was read"))
    if trajectory:
        subs = len(trajectory.get("subagent_trajectories") or [])
        lines.append(
            _line(
                "pass",
                "trajectory",
                f"{trajectory.get('schema_version')}, {len(trajectory.get('steps') or [])} "
                f"steps, {len(calls)} tool calls, {subs} subagents",
            )
        )
    else:
        lines.append(_line("fail", "trajectory", "no trajectory"))
    return lines, root is not None


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
            "esf.json omits the input messages of LLM calls after each agent's first; the raw "
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
        source: str = typer.Option(
            ..., "--source", help="A trace source name (evalkit trace-sources list)."
        ),
        project: str = typer.Option(..., "--project", help="The project name in the trace source."),
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
        if session_id is not None:
            body = client.post(
                f"/api/agents/{encode(slug)}/sessions/import",
                json={"source": source, "project": project, "session_id": session_id},
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
            json={"source": source, "project": project, **window},
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
        file: Path = typer.Argument(..., exists=True, dir_okay=False, help="Mapping JSON file."),
        activate: bool = typer.Option(
            False,
            "--activate",
            help="Also make it the active mapping, which every later run uses.",
        ),
    ):
        """Upload a mapping document as the agent's draft.

        Example: evalkit traces mapping push research-agent eval/span-mapping.json
        """
        document = json.loads(file.read_text())
        body = (
            client_factory()
            .put(
                f"/api/agents/{encode(agent_slug(agent))}/mappings/draft",
                params={"activate": str(activate).lower()},
                json=document,
            )
            .json()
        )
        typer.echo(f"{body['status']} v{body['version']} for {agent}")

    @mapping_app.command("list")
    def mapping_list(agent: str):
        """List an agent's mapping versions. Example: evalkit traces mapping list research-agent"""
        rows = client_factory().get(f"/api/agents/{encode(agent_slug(agent))}/mappings").json()
        for row in rows:
            typer.echo(
                f"v{row['version']:<4} {row['status']:<8} by {row['created_by'] or '-':<16} "
                f"{row['created_at']}"
            )
