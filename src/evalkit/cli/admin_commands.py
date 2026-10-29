import json
import os
from collections.abc import Callable
from pathlib import Path

import typer

from evalkit.cli.client import ApiClient, ApiError
from evalkit.client.api import encode
from evalkit.core.agents import explicit
from evalkit.core.workspace import parse_reference

workspaces_app = typer.Typer(no_args_is_help=True, help="Workspaces and their members.")
members_app = typer.Typer(no_args_is_help=True, help="Writers and reviewers of a shared workspace.")
workspaces_app.add_typer(members_app, name="members")
agents_app = typer.Typer(no_args_is_help=True, help="Agents: create, edit, list, archive.")
trace_sources_app = typer.Typer(
    no_args_is_help=True, help="Third-party trace stores for evalkit traces import (admin only)."
)
evaluators_app = typer.Typer(
    no_args_is_help=True, help="Remote evaluators (Custom Evaluator API endpoints) per workspace."
)
llm_providers_app = typer.Typer(
    no_args_is_help=True,
    help="LLM providers: OpenAI-compatible endpoints the server's judge calls (admin only).",
)
judge_default_app = typer.Typer(
    no_args_is_help=True, help="The judge experiments use unless they pick their own."
)

ENDPOINT_NOTE = (
    "Check this URL before saving. The server at this address receives the API key you "
    "enter here and the data evalkit sends it: cases, answers and trajectories."
)
API_KEY_ENV = typer.Option(
    None,
    "--api-key-env",
    help="Env var on this machine that holds the API key; the key is sent once and "
    "stored encrypted.",
)
CLEAR_API_KEY = typer.Option(False, "--clear-api-key", help="Remove the stored API key.")

ROLE = typer.Option(..., "--role", help="writer or reviewer.")


def _provides(pairs: list[str]) -> dict[str, bool] | None:
    if not pairs:
        return None
    parsed: dict[str, bool] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or value not in ("true", "false"):
            raise ApiError(f"--provides takes key=true or key=false, got {pair!r}")
        parsed[key] = value == "true"
    return parsed


def _settings(values: dict[str, int | None], provides: list[str]) -> dict:
    settings: dict = {key: value for key, value in values.items() if value is not None}
    flags = _provides(provides)
    if flags is not None:
        settings["provides"] = flags
    return settings


def _print_agent(agent: dict) -> None:
    typer.echo(f"{agent['ref']}  ({agent['workspace_type']}, driven by {agent['driver']})")
    typer.echo(f"  harness_url        {agent['harness_url'] or '-'}")
    typer.echo(f"  trajectory_source  {agent['trajectory_source']}")
    typer.echo(f"  repo_url           {agent['repo_url'] or '-'}")
    for key, value in agent["settings"].items():
        typer.echo(f"  {key:<18} {'-' if value is None else value}")
    if agent["workspace_type"] == "shared":
        typer.echo(f"  api key            {'set' if agent['has_api_key'] else 'not set'}")
    if agent["archived"]:
        typer.echo("  archived")


def register(
    app: typer.Typer, client_factory: Callable[[], ApiClient], whoami: Callable[[], str]
) -> None:
    app.add_typer(workspaces_app, name="workspaces")
    app.add_typer(agents_app, name="agents")
    app.add_typer(evaluators_app, name="evaluators")
    app.add_typer(trace_sources_app, name="trace-sources")
    app.add_typer(llm_providers_app, name="llm-providers")
    app.add_typer(judge_default_app, name="judge-default")

    def env_value(name: str | None) -> str | None:
        if name is None:
            return None
        if name not in os.environ:
            raise ApiError(f"--api-key-env names {name}, which is not set")
        return os.environ[name]

    def key_change(api_key_env: str | None, clear: bool) -> dict:
        if api_key_env is not None and clear:
            raise ApiError("pass --api-key-env or --clear-api-key, not both")
        if clear:
            return {"api_key": None}
        if api_key_env is not None:
            return {"api_key": env_value(api_key_env)}
        return {}

    @trace_sources_app.command("create")
    def trace_sources_create(
        name: str,
        url: str = typer.Option(..., "--url", help=ENDPOINT_NOTE),
        provider: str = typer.Option("phoenix", "--provider"),
        api_key_env: str | None = typer.Option(
            None, "--api-key-env", help="Env var on this machine that holds the API key."
        ),
    ):
        """Register a trace source. The key is sent once and stored encrypted.

        Example: evalkit trace-sources create phx --url https://phx --api-key-env PHX_KEY
        """
        body = {"name": name, "provider": provider, "url": url, "api_key": env_value(api_key_env)}
        created = client_factory().post("/api/trace-sources", json=body).json()
        typer.echo(f"created trace source {created['name']} ({created['provider']})")

    @trace_sources_app.command("edit")
    def trace_sources_edit(
        name: str,
        url: str | None = typer.Option(None, "--url", help=ENDPOINT_NOTE),
        api_key_env: str | None = typer.Option(None, "--api-key-env"),
    ):
        """Change a trace source's URL or key.

        Example: evalkit trace-sources edit phx --url https://x
        """
        body = {"url": url, "api_key": env_value(api_key_env)}
        client_factory().patch(f"/api/trace-sources/{encode(name)}", json=body)
        typer.echo(f"edited trace source {name}")

    @trace_sources_app.command("list")
    def trace_sources_list():
        """List trace sources; keys are never shown. Example: evalkit trace-sources list"""
        for row in client_factory().get("/api/trace-sources").json():
            typer.echo(f"{row['name']:<24} {row['provider']:<8} {row['url']}")

    @trace_sources_app.command("archive")
    def trace_sources_archive(name: str):
        """Archive a trace source. Example: evalkit trace-sources archive phoenix-prod"""
        client_factory().post(f"/api/trace-sources/{encode(name)}/archive")
        typer.echo(f"archived trace source {name}")

    @evaluators_app.command("create")
    def evaluators_create(
        name: str,
        endpoint: str = typer.Option(..., "--endpoint", help=ENDPOINT_NOTE),
        kind: str = typer.Option("score", "--kind", help="score or gauge."),
        timeout: int = typer.Option(120, "--timeout", help="Seconds."),
        workspace: str | None = typer.Option(
            None, "--workspace", help="Default: your personal workspace."
        ),
        api_key_env: str | None = API_KEY_ENV,
    ):
        """Register a remote evaluator. In a shared workspace its key is stored on the
        server (--api-key-env); in your personal workspace it goes in evalkit.yaml
        credentials.evaluators.

        Runs score with the evaluators in the agent's workspace.
        Example: evalkit evaluators create compliance --workspace dev --endpoint https://x/eval
        """
        body = {
            "workspace": workspace or whoami(),
            "name": name,
            "kind": kind,
            "settings": {"endpoint": endpoint, "timeout_seconds": timeout},
            **key_change(api_key_env, False),
        }
        created = client_factory().post("/api/evaluators", json=body).json()
        typer.echo(f"created {created['ref']} ({created['kind']})")

    @evaluators_app.command("edit")
    def evaluators_edit(
        ref: str,
        endpoint: str | None = typer.Option(None, "--endpoint", help=ENDPOINT_NOTE),
        timeout: int | None = typer.Option(None, "--timeout"),
        api_key_env: str | None = API_KEY_ENV,
        clear_api_key: bool = CLEAR_API_KEY,
    ):
        """Change a remote evaluator's endpoint, timeout or API key. A new endpoint clears
        the stored key unless --api-key-env sets one in the same call.

        Example: evalkit evaluators edit compliance@dev --api-key-env COMPLIANCE_KEY
        """
        resolved = ref_of(ref)
        client = client_factory()
        current = client.get(f"/api/evaluators/{encode(resolved)}").json()
        settings = {
            **current["settings"],
            **{
                k: v
                for k, v in {"endpoint": endpoint, "timeout_seconds": timeout}.items()
                if v is not None
            },
        }
        edited = client.patch(
            f"/api/evaluators/{encode(resolved)}",
            json={"settings": settings, **key_change(api_key_env, clear_api_key)},
        )
        typer.echo(f"edited {edited.json()['ref']}")

    @evaluators_app.command("show")
    def evaluators_show(ref: str):
        """Show a remote evaluator. Example: evalkit evaluators show compliance@dev"""
        row = client_factory().get(f"/api/evaluators/{encode(ref_of(ref))}").json()
        typer.echo(f"{row['ref']}  ({row['kind']})")
        typer.echo(f"  endpoint  {row['settings']['endpoint']}")
        typer.echo(f"  timeout   {row['settings']['timeout_seconds']}s")
        typer.echo(f"  api key   {'set' if row['has_api_key'] else 'not set'}")
        if row["archived"]:
            typer.echo("  archived")

    @evaluators_app.command("list")
    def evaluators_list(workspace: str | None = typer.Option(None, "--workspace")):
        """List remote evaluators. Example: evalkit evaluators list --workspace dev"""
        params = {"workspace": workspace} if workspace else {}
        for row in client_factory().get("/api/evaluators", params=params).json():
            typer.echo(f"{row['ref']:<32} {row['kind']:<6} {row['settings']['endpoint']}")

    @evaluators_app.command("archive")
    def evaluators_archive(ref: str):
        """Archive a remote evaluator. Example: evalkit evaluators archive compliance@dev"""
        resolved = ref_of(ref)
        client_factory().post(f"/api/evaluators/{encode(resolved)}/archive")
        typer.echo(f"archived {resolved}")

    @evaluators_app.command("restore")
    def evaluators_restore(ref: str):
        """Restore an archived remote evaluator.

        Example: evalkit evaluators restore compliance@dev
        """
        resolved = ref_of(ref)
        client_factory().post(f"/api/evaluators/{encode(resolved)}/restore")
        typer.echo(f"restored {resolved}")

    @llm_providers_app.command("create")
    def llm_providers_create(
        name: str,
        base_url: str = typer.Option(..., "--base-url", help=ENDPOINT_NOTE),
        api_key_env: str | None = API_KEY_ENV,
    ):
        """Register an OpenAI-compatible LLM provider (admin).

        Example: evalkit llm-providers create openai --base-url https://api.openai.com/v1
        """
        body = {"name": name, "base_url": base_url, **key_change(api_key_env, False)}
        created = client_factory().post("/api/llm-providers", json=body).json()
        typer.echo(f"created LLM provider {created['name']} ({created['base_url']})")

    @llm_providers_app.command("edit")
    def llm_providers_edit(
        name: str,
        base_url: str | None = typer.Option(None, "--base-url", help=ENDPOINT_NOTE),
        api_key_env: str | None = API_KEY_ENV,
        clear_api_key: bool = CLEAR_API_KEY,
    ):
        """Change a provider's base URL or API key (admin). A new base URL clears the
        stored key unless --api-key-env sets one in the same call.

        Example: evalkit llm-providers edit openai --api-key-env OPENAI_API_KEY
        """
        body = {"base_url": base_url, **key_change(api_key_env, clear_api_key)}
        client_factory().patch(f"/api/llm-providers/{encode(name)}", json=body)
        typer.echo(f"edited LLM provider {name}")

    @llm_providers_app.command("list")
    def llm_providers_list(
        archived: bool = typer.Option(False, "--archived", help="Include archived providers."),
    ):
        """List LLM providers; keys are never shown. Example: evalkit llm-providers list"""
        rows = client_factory().get("/api/llm-providers", params={"include_archived": archived})
        for row in rows.json():
            key = "key set" if row["has_api_key"] else "no key"
            state = "archived" if row["archived"] else ""
            typer.echo(f"{row['name']:<24} {row['base_url']:<40} {key:<8} {state}")

    @llm_providers_app.command("show")
    def llm_providers_show(name: str):
        """Show an LLM provider. Example: evalkit llm-providers show openai"""
        row = client_factory().get(f"/api/llm-providers/{encode(name)}").json()
        typer.echo(row["name"])
        typer.echo(f"  base_url  {row['base_url']}")
        typer.echo(f"  api key   {'set' if row['has_api_key'] else 'not set'}")
        if row["archived"]:
            typer.echo("  archived")

    @llm_providers_app.command("archive")
    def llm_providers_archive(name: str):
        """Archive an LLM provider (admin). Example: evalkit llm-providers archive openai"""
        client_factory().post(f"/api/llm-providers/{encode(name)}/archive")
        typer.echo(f"archived LLM provider {name}")

    @llm_providers_app.command("restore")
    def llm_providers_restore(name: str):
        """Restore an archived LLM provider (admin).

        Example: evalkit llm-providers restore openai
        """
        client_factory().post(f"/api/llm-providers/{encode(name)}/restore")
        typer.echo(f"restored LLM provider {name}")

    @judge_default_app.command("show")
    def judge_default_show():
        """Show the default judge. Example: evalkit judge-default show"""
        for key, value in client_factory().get("/api/judge-default").json().items():
            typer.echo(f"{key:<18} {value}")

    @judge_default_app.command("set")
    def judge_default_set(
        provider: str = typer.Option(..., "--provider", help="An LLM provider name."),
        model: str = typer.Option(..., "--model"),
        effort: str = typer.Option("high", "--effort", help="Reasoning effort."),
        max_tokens: int = typer.Option(32768, "--max-tokens"),
        concurrency: int = typer.Option(16, "--concurrency"),
    ):
        """Set the judge experiments use unless they pick their own (admin).

        Example: evalkit judge-default set --provider openai --model gpt-5
        """
        client = client_factory()
        base_url = client.get(f"/api/llm-providers/{encode(provider)}").json()["base_url"]
        judge = {
            "base_url": base_url,
            "model": model,
            "reasoning_effort": effort,
            "max_tokens": max_tokens,
            "concurrency": concurrency,
        }
        client.put("/api/judge-default", json=judge)
        typer.echo(f"default judge: {model} on {provider} ({base_url})")

    def ref_of(text: str) -> str:
        try:
            return explicit(parse_reference(text, whoami()))
        except ValueError as exc:
            raise ApiError(str(exc)) from exc

    @agents_app.command("create")
    def agents_create(
        name: str,
        workspace: str | None = typer.Option(
            None, "--workspace", help="Default: your personal workspace."
        ),
        harness_url: str | None = typer.Option(
            None,
            "--harness-url",
            help=f"Omit for a code-driven agent (SDK runs only). {ENDPOINT_NOTE}",
        ),
        trajectory_source: str = typer.Option("atif", "--trajectory-source", help="atif or otel."),
        mapping: Path | None = typer.Option(
            None,
            "--mapping",
            exists=True,
            dir_okay=False,
            help="Span mapping JSON; required with --trajectory-source otel.",
        ),
        repo_url: str | None = typer.Option(None, "--repo-url"),
        max_concurrent_runs: int | None = typer.Option(None, "--max-concurrent-runs"),
        run_timeout: int | None = typer.Option(None, "--run-timeout"),
        poll_interval: int | None = typer.Option(None, "--poll-interval"),
        stop_grace: int | None = typer.Option(None, "--stop-grace"),
        trajectory_wait: int | None = typer.Option(None, "--trajectory-wait"),
        provides: list[str] = typer.Option([], "--provides", help="key=true|false, repeatable."),
        api_key_env: str | None = API_KEY_ENV,
    ):
        """Create an agent. In a shared workspace its key is stored on the server
        (--api-key-env); in your personal workspace it goes in evalkit.yaml
        credentials.agents.

        Example: evalkit agents create ouragent --workspace dev --harness-url https://agent/evalkit/v1
        """
        settings = _settings(
            {
                "max_concurrent_runs": max_concurrent_runs,
                "run_timeout_seconds": run_timeout,
                "poll_interval_seconds": poll_interval,
                "stop_grace_seconds": stop_grace,
                "trajectory_wait_seconds": trajectory_wait,
            },
            provides,
        )
        body = {
            "workspace": workspace or whoami(),
            "name": name,
            "harness_url": harness_url,
            "trajectory_source": trajectory_source,
            "repo_url": repo_url,
            "settings": settings,
            "mapping": json.loads(mapping.read_text()) if mapping else None,
            **key_change(api_key_env, False),
        }
        _print_agent(client_factory().post("/api/agents", json=body).json())
        if mapping:
            typer.echo(f"  mapping            v1, active (from {mapping.name})")

    @agents_app.command("edit")
    def agents_edit(
        ref: str,
        harness_url: str | None = typer.Option(None, "--harness-url", help=ENDPOINT_NOTE),
        repo_url: str | None = typer.Option(None, "--repo-url"),
        trajectory_source: str | None = typer.Option(
            None, "--trajectory-source", help="atif or otel."
        ),
        mapping: Path | None = typer.Option(
            None,
            "--mapping",
            exists=True,
            dir_okay=False,
            help="Span mapping JSON, made the active version. Needed to switch to otel "
            "unless the agent already has an active mapping.",
        ),
        max_concurrent_runs: int | None = typer.Option(None, "--max-concurrent-runs"),
        run_timeout: int | None = typer.Option(None, "--run-timeout"),
        poll_interval: int | None = typer.Option(None, "--poll-interval"),
        stop_grace: int | None = typer.Option(None, "--stop-grace"),
        trajectory_wait: int | None = typer.Option(None, "--trajectory-wait"),
        provides: list[str] = typer.Option([], "--provides", help="key=true|false, repeatable."),
        api_key_env: str | None = API_KEY_ENV,
        clear_api_key: bool = CLEAR_API_KEY,
    ):
        """Change an agent's harness URL, API key, repo URL, trajectory source or settings;
        other values stay. A new harness URL clears the stored key unless --api-key-env
        sets one in the same call. Switching the trajectory source is refused while one of
        the agent's experiments is running; experiments that already ran keep the source
        they ran with.

        Example: evalkit agents edit mybot --trajectory-source otel --mapping gen_ai.json
        """
        path = f"/api/agents/{encode(ref_of(ref))}"
        client = client_factory()
        current = client.get(path).json()
        changed = _settings(
            {
                "max_concurrent_runs": max_concurrent_runs,
                "run_timeout_seconds": run_timeout,
                "poll_interval_seconds": poll_interval,
                "stop_grace_seconds": stop_grace,
                "trajectory_wait_seconds": trajectory_wait,
            },
            provides,
        )
        body: dict = {"settings": {**current["settings"], **changed}}
        if harness_url is not None:
            body["harness_url"] = harness_url
        if repo_url is not None:
            body["repo_url"] = repo_url
        if trajectory_source is not None:
            body["trajectory_source"] = trajectory_source
        if mapping is not None:
            body["mapping"] = json.loads(mapping.read_text())
        body.update(key_change(api_key_env, clear_api_key))
        _print_agent(client.patch(path, json=body).json())

    @agents_app.command("show")
    def agents_show(ref: str):
        """Show an agent's settings. Example: evalkit agents show ouragent@dev"""
        _print_agent(client_factory().get(f"/api/agents/{encode(ref_of(ref))}").json())

    @agents_app.command("list")
    def agents_list(
        workspace: str | None = typer.Option(None, "--workspace"),
        archived: bool = typer.Option(False, "--archived", help="Include archived agents."),
    ):
        """List agents. Example: evalkit agents list --workspace dev"""
        params: dict = {"include_archived": archived}
        if workspace:
            params["workspace"] = workspace
        for agent in client_factory().get("/api/agents", params=params).json():
            state = "archived" if agent["archived"] else ""
            typer.echo(
                f"{agent['ref']:<32} {agent['driver']:<7} {agent['trajectory_source']:<5} "
                f"{agent['harness_url'] or '-'} {state}"
            )

    @agents_app.command("archive")
    def agents_archive(ref: str):
        """Archive an agent: it no longer runs; its runs and results stay. Undo it with
        evalkit agents restore.

        Example: evalkit agents archive ouragent@dev
        """
        resolved = ref_of(ref)
        client_factory().post(f"/api/agents/{encode(resolved)}/archive")
        typer.echo(f"archived {resolved}")

    @agents_app.command("restore")
    def agents_restore(ref: str):
        """Restore an archived agent, so it can run again.

        Example: evalkit agents restore ouragent@dev
        """
        resolved = ref_of(ref)
        client_factory().post(f"/api/agents/{encode(resolved)}/restore")
        typer.echo(f"restored {resolved}")

    @workspaces_app.command("list")
    def workspaces_list(
        archived: bool = typer.Option(False, "--archived", help="Include archived workspaces."),
    ):
        """List workspaces. Example: evalkit workspaces list"""
        rows = client_factory().get("/api/workspaces", params={"include_archived": archived})
        for ws in rows.json():
            state = "archived" if ws["archived"] else ""
            owner = ws["owner"] or "-"
            typer.echo(f"{ws['name']:<24} {ws['type']:<9} owner {owner:<16} {state}")

    @workspaces_app.command("create")
    def workspaces_create(name: str):
        """Create a shared workspace (admin only). Example: evalkit workspaces create dev"""
        client_factory().post("/api/workspaces", json={"name": name})
        typer.echo(f"created @{name}")

    @workspaces_app.command("rename")
    def workspaces_rename(name: str, new_name: str):
        """Rename a shared workspace (admin only). Example: evalkit workspaces rename dev staging"""
        body = client_factory().patch(f"/api/workspaces/{name}", json={"name": new_name}).json()
        typer.echo(f"renamed @{name} to @{new_name}; now update:")
        for line in body["update"]:
            typer.echo(f"  - {line}")

    @workspaces_app.command("promote-to")
    def workspaces_promote_to(
        source: str,
        target: str | None = typer.Argument(None, help="The promotion target workspace."),
        none: bool = typer.Option(False, "--none", help="Clear the link."),
    ):
        """Make TARGET the promotion target of SOURCE (admin only).

        Cases approved in a suite of SOURCE land, approved, in the same-named suite of
        TARGET. TARGET is then fed by promotion only: syncs and review decisions there
        are refused. One target per source, no chains.
        Example: evalkit workspaces promote-to dev prod · evalkit workspaces promote-to dev --none
        """
        if none == (target is not None):
            raise ApiError("pass a target workspace or --none")
        body = (
            client_factory().patch(f"/api/workspaces/{source}", json={"promotes_to": target}).json()
        )
        typer.echo(f"{source} promotes to {body['promotes_to'] or '-'}")
        for line in body["update"]:
            typer.echo(f"  update: {line}")

    @workspaces_app.command("archive")
    def workspaces_archive(name: str):
        """Archive a workspace: it can no longer be written to.

        Admins archive shared workspaces; a user may archive their own.
        Example: evalkit workspaces archive dev
        """
        client_factory().post(f"/api/workspaces/{name}/archive")
        typer.echo(f"archived @{name}")

    @workspaces_app.command("restore")
    def workspaces_restore(name: str):
        """Restore an archived workspace. Example: evalkit workspaces restore dev"""
        client_factory().post(f"/api/workspaces/{name}/restore")
        typer.echo(f"restored @{name}")

    @members_app.command("list")
    def members_list(
        workspace: str,
        role: str | None = typer.Option(None, "--role", help="writer or reviewer."),
    ):
        """List a workspace's members. Example: evalkit workspaces members list dev"""
        params = {"role": role} if role else {}
        for m in client_factory().get(f"/api/workspaces/{workspace}/members", params=params).json():
            typer.echo(f"{m['username']:<24} {m['role']:<9} granted by {m['granted_by']}")

    @members_app.command("add")
    def members_add(workspace: str, username: str, role: str = ROLE):
        """Grant a role on a shared workspace (admin only).

        Example: evalkit workspaces members add dev alice --role reviewer
        """
        client_factory().post(
            f"/api/workspaces/{workspace}/members", json={"username": username, "role": role}
        )
        typer.echo(f"{username} is a {role} of @{workspace}")

    @members_app.command("remove")
    def members_remove(workspace: str, username: str, role: str = ROLE):
        """Revoke a role (admin only).

        Example: evalkit workspaces members remove dev alice --role reviewer
        """
        client_factory().delete(f"/api/workspaces/{workspace}/members/{username}/{role}")
        typer.echo(f"{username} is no longer a {role} of @{workspace}")
