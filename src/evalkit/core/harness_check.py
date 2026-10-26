"""Live verification of a client's Harness API implementation.

Runs the spec's checks and returns a per-capability pass/fail table:
capabilities shape, the probe input against the declared input_schema, a
trivial run to a terminal status (carrying one input file where accepted),
trajectory ATIF validation, then workspace files / run listing / stop where
declared.
"""

from datetime import UTC, datetime, timedelta

import httpx

from evalkit.contracts.atif import TrajectoryValidationError, subagent_link_warnings
from evalkit.contracts.harness import FileRef, RunListParams, input_errors
from evalkit.core.agents import AgentDefinition
from evalkit.core.blob import BlobStore
from evalkit.core.harness import HarnessClient, RunTimeout
from evalkit.core.paths import UnsafePathError
from evalkit.core.traces.poll import poll_trajectory
from evalkit.core.traces.sessions import SessionReader

CHECK_INPUT = {"question": "Hi. Reply with one word."}
CHECK_FILE = ("evalkit-check.txt", b"evalkit harness check\n")
CHECK_FILE_CONTEXT = "A one-line text file evalkit attaches to its harness check."
CHECK_BLOB_KEY = "checks/evalkit-check.txt"
IGNORES_TIME_FILTER = "ignores since/until; evalkit filters the runs itself"


def _entry(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail}


async def check_harness(
    agent: AgentDefinition,
    api_key: str | None,
    blob_store: BlobStore,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    sessions: SessionReader | None = None,
    agent_slug: str | None = None,
) -> dict[str, dict]:
    client = HarnessClient(agent, api_key, transport=transport)
    report: dict[str, dict] = {}
    try:
        try:
            caps = await client.capabilities()
            detail = f"contract_version={caps.contract_version} agent={caps.agent}"
            if caps.revision:
                rev = caps.revision[:8]
                detail += f" revision={rev}"
                if caps.branch:
                    detail += f" branch={caps.branch}"
            report["capabilities"] = _entry("pass", detail)
        except (httpx.HTTPError, ValueError) as exc:
            report["capabilities"] = _entry("fail", str(exc))
            return report

        probe = caps.check_input or CHECK_INPUT
        schema_name = "input_schema" if caps.input_schema else "the default input schema"
        if caps.check_input is not None:
            report["input_schema"] = _entry("pass", f"check_input valid against {schema_name}")
        elif errors := input_errors(caps.effective_input_schema, CHECK_INPUT):
            report["input_schema"] = _entry(
                "fail",
                f"default probe {CHECK_INPUT} fails input_schema ({errors[0]}); "
                "declare check_input",
            )
        else:
            report["input_schema"] = _entry("pass", f"default probe valid against {schema_name}")

        input_files: list[tuple[str, bytes]] | None = None
        input_refs: list[FileRef] | None = None
        if caps.accepts.file_urls:
            await blob_store.put_object(CHECK_BLOB_KEY, CHECK_FILE[1])
            input_refs = [
                FileRef(
                    name=CHECK_FILE[0],
                    url=await blob_store.object_url(CHECK_BLOB_KEY),
                    context=CHECK_FILE_CONTEXT,
                )
            ]
        elif caps.accepts.files:
            input_files = [CHECK_FILE]
            input_refs = [FileRef(name=CHECK_FILE[0], context=CHECK_FILE_CONTEXT)]
        run_id: str | None = None
        try:
            run_id = await client.start_run(probe, files=input_files, file_refs=input_refs)
            status = await client.poll_until_terminal(run_id)
            carried = [
                kind
                for kind, value in (("an answer", status.answer), ("an output", status.output))
                if value
            ]
            if status.status == "completed" and carried:
                report["run"] = _entry(
                    "pass", f"run {run_id} completed with {' and '.join(carried)}"
                )
            else:
                report["run"] = _entry("fail", f"run ended {status.status}, error={status.error}")
        except (httpx.HTTPError, RunTimeout) as exc:
            report["run"] = _entry("fail", str(exc))
        except UnsafePathError:
            report["run"] = _entry("fail", f"run id {run_id!r} must be one plain path segment")
            run_id = None

        if not caps.accepts.file_input:
            report["files"] = _entry("skipped", "accepts.files=false, accepts.file_urls=false")
        elif run_id is None:
            report["files"] = _entry("fail", report["run"]["detail"])
        elif caps.accepts.file_urls:
            report["files"] = _entry("pass", f"{CHECK_FILE[0]} fetched by url")
        else:
            report["files"] = _entry("pass", f"{CHECK_FILE[0]} accepted on the run request")

        if agent.is_otel:
            report["trajectory"] = _entry("pass", "from traces")
            if run_id is None:
                report["session"] = _entry("fail", "no run to poll a session for")
            elif sessions is None:
                report["session"] = _entry("skipped", "no session reader for this check")
            else:
                doc, session_status = await poll_trajectory(
                    sessions,
                    agent_slug or agent.ref,
                    run_id,
                    agent.settings.trajectory_wait_seconds,
                )
                session = await sessions.trajectory(agent_slug or agent.ref, run_id)
                detail = f"{session_status}: session {run_id}, {session.span_count} spans"
                if doc is not None:
                    detail += f", {len(doc.get('steps') or [])} steps"
                report["session"] = _entry(
                    {"valid": "pass", "partial": "warn"}.get(session_status, "fail"), detail
                )
        elif not caps.provides.trajectory:
            report["trajectory"] = _entry("skipped", "provides.trajectory=false")
        elif run_id is None:
            report["trajectory"] = _entry("fail", "no run to fetch a trajectory for")
        else:
            try:
                trajectory = await client.trajectory(run_id)
                detail = f"valid {trajectory.schema_version} with {len(trajectory.steps)} steps"
                warnings = subagent_link_warnings(trajectory)
                report["trajectory"] = (
                    _entry("warn", "; ".join([detail, *warnings]))
                    if warnings
                    else _entry("pass", detail)
                )
            except (httpx.HTTPError, TrajectoryValidationError) as exc:
                report["trajectory"] = _entry("fail", str(exc))

        if not caps.provides.workspace:
            report["workspace"] = _entry("skipped", "provides.workspace=false")
        elif run_id is None:
            report["workspace"] = _entry("fail", "no run to list files for")
        else:
            try:
                files = await client.list_files(run_id)
                report["workspace"] = _entry("pass", f"{len(files.files)} file(s) listed")
            except httpx.HTTPError as exc:
                report["workspace"] = _entry("fail", str(exc))

        if not caps.provides.run_listing:
            report["run_listing"] = _entry("skipped", "provides.run_listing=false")
        else:
            try:
                runs = await client.list_runs(RunListParams(limit=5))
                future = await client.list_runs(
                    RunListParams(limit=5, since=datetime.now(UTC) + timedelta(days=1))
                )
                report["run_listing"] = (
                    _entry("warn", f"total={runs.total}; {IGNORES_TIME_FILTER}")
                    if future.runs
                    else _entry("pass", f"total={runs.total}")
                )
            except httpx.HTTPError as exc:
                report["run_listing"] = _entry("fail", str(exc))

        if not caps.accepts.stop:
            report["stop"] = _entry("skipped", "accepts.stop=false")
        else:
            try:
                stop_run_id = await client.start_run(probe)
                await client.stop(stop_run_id)
                report["stop"] = _entry("pass", f"run {stop_run_id} stopped")
            except (httpx.HTTPError, UnsafePathError) as exc:
                report["stop"] = _entry("fail", str(exc))
    finally:
        await client.aclose()
    return report
