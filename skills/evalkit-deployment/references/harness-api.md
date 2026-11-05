# Implementing the Harness API (v1)

The HTTP contract evalkit uses to drive the client's agent. Base URL is the
agent's `--harness-url` (convention: `https://<host>/evalkit/v1`). Every
request from evalkit carries the agent's key in an `x-api-key: <key>` header
(the server sends the shared agent's stored key; a local run sends
`credentials.agents` from the developer's `evalkit.yaml`) — reject anything else with 401. Exact request/response schemas: run
`evalkit contracts export --target <dir>` to write `harness-api-v1.json`
(OpenAPI, generated from the source-of-truth pydantic models) plus the other
contract files, matching the installed evalkit version.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /capabilities` | Self-declaration: `contract_version`, `agent`, `provides` (trajectory, tool_calls, thinking, token_usage, artifacts, workspace, run_listing), `accepts` (files, file_urls, model_override, stop), `limits.max_concurrent_runs`, optional `input_schema` and `check_input` (below). Snapshotted onto every experiment. |
| `POST /runs` | Start one agent run in a fresh session. Body: `{input, model?, metadata?, files?}`. `input` is the case input exactly as written; evalkit never rewrites it. When the case has fixture files and you declare `accepts.file_urls`, the JSON body carries `files: [{name, url, context}]` and you fetch each url yourself (the url is evalkit's blob store url plus the object key, e.g. `s3://bucket/fixtures/sha256/<sha256>`; your agent harness system must have access). If you declare only `accepts.files`, evalkit instead sends `multipart/form-data` with a `payload` JSON part (carrying `files: [{name, context}]`, no urls) plus one part per file. `context` is the file's description from the suite's fixture registry; show it to your agent however suits it (for example, an "Attached files" block ahead of the question). URLs win when both are declared; a harness declaring neither never receives fixture cases. Return `201 {run_id}` — your own id, typically the session id. For an agent whose trajectory source is `otel` this `run_id` is the session evalkit polls; a queue-backed harness whose worker opens its own session returns that session id here, or stamps this `run_id` on the job's spans in the attribute its mapping's `session_id` rule names. |
| `GET /runs/{id}` | Poll status: `queued | running | completed | failed | cancelled`, plus `answer` and/or `output` (a completed run carries at least one; below), optional `artifacts` list, optional `error` (a completed run may carry a non-fatal error, e.g. a partial answer). |
| `POST /runs/{id}/stop` | Cancel a run. Called on timeout when `accepts.stop`. Return as soon as the stop is requested. Report a terminal status from `GET /runs/{id}` once `GET /runs/{id}/trajectory` returns the run's full trajectory. evalkit keeps polling for up to `stop_grace_seconds` (agent config, default 120), then fetches the trajectory. |
| `GET /runs/{id}/trajectory` | The run's trajectory as an ATIF document (below). Only meaningful after a terminal status; evalkit fetches it once and persists it. |
| `GET /runs?query=&user_id=&org_id=&limit=&offset=&since=&until=` | Optional (`run_listing`): list runs/sessions, newest first, for the UI browser and `evalkit runs list` / `runs pull`. `{runs: [{run_id, title, user_id, org_id, created_at}], total}`. `since` / `until` (ISO 8601) keep runs created at or after `since` and before `until`; evalkit drops runs outside the window itself when a harness ignores them, and `evalkit harness check` warns. A harness without multi-tenancy returns fixed dummy user/org ids. Returned ids must work with the other `/runs/{id}` endpoints. A harness that keeps several of its own runs per session (for example one per chat message) and answers each with the whole session sets `session_id` on every item: evalkit's run is the session, so it lists items sharing a `session_id` once, as the newest. |
| `GET /runs/{id}/files` and `GET /runs/{id}/files/{path}` | Optional (`workspace`): workspace listing `{files: [{path, size, content_type}]}` and raw file bytes. Powers the UI workspace viewer and artifact collection. |

Notes:

- `metadata` on `POST /runs` carries `{experiment_id, case_id, repetition}` so
  the harness can stamp its own sessions/logs and keep eval traffic
  distinguishable from app traffic (runs outside any experiment). Ignoring it
  is allowed.
- Timeouts, polling intervals, and retries are evalkit-side config, not
  contract fields.

## Typed agents: `output`, `input_schema`, `check_input`

For agents that take typed inputs and return typed results (classifiers,
taggers, extractors), rather than answering a question in prose:

- `output` on `GET /runs/{id}`: the agent's structured result as a JSON
  object, for example `{"label": "EVENT"}`. Send it instead of JSON-encoding
  the result into `answer`. A completed run may carry `output` only, `answer`
  only, or both. evalkit stores `output` as-is and passes it parsed to custom
  evaluators as `run.output`; text-based evaluators (rubric judges) and the
  Custom Evaluator API's `run.answer` get `output` as canonical JSON (sorted
  keys) when `answer` is absent. An evalkit older than 0.2.0 still requires
  `answer`.
- `input_schema` in capabilities: a JSON Schema (draft 2020-12 unless its own
  `$schema` says otherwise) for the `input` you accept. It never covers
  `files`, which evalkit sends separately. Without one, evalkit assumes the
  default question-and-answer schema, `{"type": "object", "required":
  ["question"], "properties": {"question": {"type": "string"}}}`, so every
  agent is checked the same way. Before an experiment starts,
  evalkit checks every selected case's input against it and refuses the
  whole experiment, listing each failing case, if any fails. Case authors can
  run the same check on demand with `evalkit lint <dir> --agent <name>`. An
  invalid schema makes the capabilities response invalid.
- `check_input` in capabilities: the input `evalkit harness check` sends in
  place of its default probe `{"question": "Hi. Reply with one word."}`.
  Declare it whenever that default doesn't fit your `input_schema` or your
  agent. It must pass `input_schema` (or the default schema), or the
  capabilities response is invalid.

## The trajectory: ATIF

evalkit accepts ATIF (Agent Trajectory Interchange Format). The spec is the
Harbor RFC, and it is the source of truth; evalkit doesn't copy it or publish a
schema of its own. Pinned version:
https://github.com/harbor-framework/harbor/blob/74cc6312018c349c6bd2400c89a0ac4983ac1085/rfcs/0001-trajectory-format.md.
Accepted `schema_version` values ship in `atif-accepted.json` (from
`evalkit contracts export`; currently `ATIF-v1.8`).

Required subset (validation fails without these):

- `schema_version`
- `steps[]`, each with `step_id` (sequential int from 1), `source`
  (`user` / `agent` / `system`), and `message` (string or content-part list)
- tool interactions as `tool_calls[]` (`tool_call_id`, `function_name`,
  `arguments`) paired to `observation.results[]` entries by `source_call_id`

Everything else is optional and capability-declared: `reasoning_content`
(thinking), per-step `metrics` (`prompt_tokens`, `completion_tokens`,
`cached_tokens`, `cost_usd`), `model_name`, `timestamp`, `final_metrics`,
`subagent_trajectories` (a list of embedded, independently valid trajectories),
`extra`.

Follow these RFC rules; evalkit's metrics depend on them:

- Set `prompt_tokens` to every input token the model processed, cached ones
  included, and `cached_tokens` to the cached part of it. evalkit prices
  `prompt_tokens - cached_tokens` at the input rate and `cached_tokens` at the
  cache-read rate. Put provider-specific counts such as cache writes in
  `metrics.extra`.
- Make the root `final_metrics` token totals cover the whole run, every
  embedded subagent included. evalkit's token gauges read the root totals.
- Give every embedded subagent a `trajectory_id`, unique within
  `subagent_trajectories`, and set `subagent_trajectory_ref:
  [{"trajectory_id": ...}]` on the result of the tool call that started it.
  Link by `trajectory_id`; `session_id` may be shared across a run.
  `evalkit harness check` warns when ids are missing, and a later release will
  require them.
- Set a subagent's `agent.name` to its type (e.g. `researcher`). evalkit
  records it next to each subagent's metrics.
- Set `agent.name` and `agent.version` on every trajectory.

### evalkit `extra` conventions

ATIF leaves room for producer metadata in `extra`. These are the only fields
evalkit reads from it. All are optional; anything else in `extra` is kept and
ignored.

| Field | On | Type | Meaning | evalkit uses it for |
|---|---|---|---|---|
| `is_error` | `ObservationResult.extra` | bool | The tool call failed. Keep the error message in `content`. | `errored_tool_calls` gauge; judges and diagnosis |
| `timestamp` | `ObservationResult.extra` | ISO 8601 string | When the result came back. | Tool time: this minus the calling step's `timestamp` |
| `error_type` | `ObservationResult.extra` | string | Your own class for an errored result (e.g. `mcp_error`, `exit_code`). Free-form. | Grouping errors without parsing message text |
| `full_output_path` | `ObservationResult.extra` | string | When `content` is a shortened version of the real output, a path your `GET /runs/{id}/files/{path}` serves with the full output. Keep `content` as what the agent saw. | `evalkit runs files <dir> <run_id> --path <it>` fetches it |
| `is_error` | `Step.extra` | bool | The turn this step belongs to failed (timed out, the model call errored, the agent crashed). Put it on the step that records the failure, often a `system` step. | `failed_turns` in the MCP `get_runs` and `get_run_stats` |
| `error` | `Step.extra` | string | The failure message, when the step's `message` is not it. | The turn's `error` in MCP `get_runs` |

Conventions:

- A turn is one user step and every step after it until the next user step.
  A multi-turn chat session is one run with several user steps.
- The final answer is NOT read from the trajectory — it comes from the run
  status response. The trajectory endpoint returns a pure ATIF document.
- Convert from your native session format at read time (a converter from your
  session log to ATIF), or record ATIF natively. Keep the converter total: a
  session your converter can't handle should fail loudly, not emit a truncated
  document.

## Verification

`evalkit harness check <name>@dev` exercises: capabilities shape → the
probe input against `input_schema` (or the default schema) → a trivial run (with
`check_input`, else "Hi. Reply with one word.") to a terminal status with an
`answer` or `output`, carrying one
small input file by url when `accepts.file_urls` or as multipart when only
`accepts.files` → trajectory ATIF validation → workspace files, run listing,
and stop where declared. All declared capabilities must pass before running
real experiments.
