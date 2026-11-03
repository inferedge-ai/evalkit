# Harness API

The Harness API is the HTTP contract a client's agent harness implements so evalkit can drive it: start a run, poll its status, serve its trajectory. Agents in a shared workspace are driven by this server; agents in a personal workspace by `evalkit run` from their owner's machine. The OpenAPI schema ships inside the CLI:

```bash
evalkit contracts export --target ./contracts   # harness-api-v1.json, atif-accepted.json, …
```

Each endpoint lets evalkit do one thing. Every Harness API has the first three; declare the rest in the capabilities response when you implement them. An agent on the SDK + traces path has no Harness API and none of these endpoints; evalkit sees what its task returns and the spans it exports.

| Endpoint                                              | Lets evalkit                                                                          | Capability flag           | Without it                                                                                                     |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `GET /capabilities`                                   | see what the harness supports and gate evaluators on it (snapshotted per experiment)  | part of every Harness API | —                                                                                                              |
| `POST /runs`                                          | invoke the agent: start one run in a fresh session with the case input and files      | part of every Harness API | —                                                                                                              |
| `GET /runs/{id}`                                      | wait for the run and read its answer, structured output, model and artifact list      | part of every Harness API | —                                                                                                              |
| `GET /runs/{id}/trajectory`                           | read the trajectory (ATIF): the run view, trajectory evaluators and token/cost gauges | `provides.trajectory`     | trajectory evaluators skip, unless the agent's trajectory source is `otel` and the trajectory comes from spans |
| `GET /runs/{id}/files`, `GET /runs/{id}/files/{path}` | read the workspace: the run's Workspace tab, downloads and artifact copies            | `provides.workspace`      | no Workspace tab; artifacts the run reports are not copied                                                     |
| `GET /runs`                                           | list the agent's sessions on the Runs page and for `evalkit runs list`/`runs pull`, including ones evalkit did not start; optional `since`/`until` filter by creation time | `provides.run_listing`    | the Runs page is empty for this agent                                                                          |
| `POST /runs/{id}/stop`                                | cancel a run that hit its time limit                                                  | `accepts.stop`            | timed-out runs keep running on the harness                                                                     |

`POST /runs` takes JSON, or multipart when the case has fixture files and the harness accepts `files` but not `file_urls`. Run status moves through `queued`, `running`, then one of `completed`, `failed`, `cancelled`.

A code-driven agent (no `harness_url`) has none of these endpoints, so its runs have no Workspace tab. To see an agent's files in evalkit, give it a Harness API with at least the files endpoints; its trajectory can still come from traces.

Every request carries `x-api-key`. Two rules matter more than the rest:

- **Capabilities honesty.** Declare only what works. evalkit neutral-skips evaluators whose inputs the harness does not provide; a false `true` produces failing scores blamed on the agent.
- **Implement stop.** evalkit calls stop on timeout when `accepts.stop` is true; a harness without it leaks resources on every timed-out run.

## Already have a harness? Keep it

A harness that already emits OpenTelemetry traces can skip the trajectory endpoint: create the agent with `--trajectory-source otel --mapping FILE` and export spans to the collector with the run id it returned from `POST /runs` in the attribute the mapping's `session_id` rule names (`session.id` for OpenInference, `gen_ai.conversation.id` for GenAI). Everything else stays: server runs, `evalkit run`, the Workspace tab. The harness declares `provides.trajectory: false`. See [Tracing](tracing.md).

The session evalkit polls is the `run_id` that `POST /runs` returns; there is no separate session field. A queue-backed agent whose worker opens its own session can return that session id as `run_id`, or stamp the `run_id` it returned on the job's spans in the mapping's session id attribute. Either way the ids must also work with the other `/runs/{id}` endpoints.

Moving to [SDK runs](sdk-runs.md) instead only makes sense to stop hosting the harness. A code-driven agent loses server-started runs and the Workspace tab, and `run_experiment` refuses an agent with a `harness_url`: keep one agent name and run it with `evalkit run`, rather than adding a second entry for `run_experiment`.

## Checking

```bash
evalkit harness check ouragent@dev
```

runs a trivial call, exercises every declared capability and prints a pass/fail table. For a harness agent whose trajectory source is `otel` the trajectory row reads "from traces" and a session row reports the collected spans.
