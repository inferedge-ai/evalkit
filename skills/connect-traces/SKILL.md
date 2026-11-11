---
name: connect-traces
description: Use when wiring a client's OpenTelemetry traces into evalkit so run_experiment or a harness run gets trajectories, when importing an agent's existing traces from a trace store such as Phoenix instead, when creating an OTel agent (it needs a span mapping), or when `evalkit traces check` shows wrong span kinds, empty messages or missing tokens.
---

# connect-traces

## Overview

evalkit scores an agent on its **trajectory**: the LLM calls, messages, tool
calls and token counts of one run. When the agent emits OpenTelemetry spans,
evalkit builds that trajectory from the spans itself: the server groups the
spans of one run into an **agent session** by the `session.id` attribute,
reads each span into the evalkit span format (**ESF**) with a **mapping**, and
converts the session to ATIF. Nothing in the client's code converts anything.

Your job ends when `evalkit traces check` shows the right span kinds,
messages, tool calls and tokens for one session. Scoring, scorecards and the
ATIF conversion are evalkit's side and need nothing from you.

## Two paths: export or import

Spans reach evalkit in one of two ways. Most of the steps are the same.

| | Export | Import |
| -- | -- | -- |
| How spans arrive | The agent sends every new span to the evalkit server (the **collector**) | evalkit pulls past spans from a **trace source**: a trace store an admin registered, today Arize Phoenix |
| Change to the agent's code | Add evalkit's exporter | None |
| What the developer gets | App runs on the Runs page, `evalkit runs pull`, case mining, and trajectories for experiments | App runs on the Runs page, `evalkit runs pull` and case mining, for the time range imported |
| Experiments (`run_experiment`, harness runs) | Get trajectories from the spans | Do not: they still need the export path or an agent that returns ATIF |

Import suits a developer who already sends traces to Phoenix and wants to
try evalkit on that history first. They can add the export path later
without redoing anything: the agent and its mapping stay the same.

Pick the path yourself, then confirm it with the developer in one line:

1. The developer wants to run experiments now → export.
2. The agent already sends traces to Phoenix (the developer said so, or the
   repository mentions `phoenix.otel` or `PHOENIX_COLLECTOR_ENDPOINT`) and `evalkit trace-sources list`
   shows a source → import first, export later. An empty list means no admin
   has registered one; the developer asks an admin to run
   `evalkit trace-sources create`, or takes the export path.
3. Otherwise → export.

For import, ask in the same message for the Phoenix project name and one
session id from it (Phoenix → the project → Sessions tab), so the developer
is interrupted once. For example: "Your traces are already in Phoenix, so I'll
import them and leave your code alone. Which Phoenix project, and one session
id from its Sessions tab?" You cannot list Phoenix projects or sessions
yourself: the trace source's URL and key live on the evalkit server.

The steps below mark where the paths differ. The sections after the steps
("An agent in another process" and later) apply to the export path only.

Traces replace the trajectory only. The files a run wrote (the run's
Workspace tab in evalkit) come from the Harness API files endpoints, which
spans do not carry. If the developer wants that tab, the agent needs a
Harness API with those endpoints (see the evalkit-usage skill).

Every OTel agent has its own mapping, set when the agent is created.
`references/` ships one per popular framework: `langchain.json`,
`crewai.json`, `agno.json`, `llamaindex.json`, `google-adk.json`,
`claude-agent-sdk.json` and `mastra.json`. Pick the agent's framework first;
[Framework mappings](../evalkit-docs/framework-mappings.md) lists which file
fits which framework (LangGraph, OpenAI Agents SDK and Pydantic AI use the
generic files as they are) and the agent-side changes each one still needs.
(`evalkit-docs/` sits beside the installed skills; in the evalkit
repository the page is `docs/user/framework-mappings.md`.)
Otherwise fall back to the two generic files: `openinference.json` for spans
with `openinference.span.kind`, and `gen_ai.json` for spans with
`gen_ai.operation.name`. Copy the one that fits and change it if the agent's
attributes differ. The mapping also holds
two run-level settings:

- `answer`: where the run's answer comes from. `root` (the usual value), a
  span name, or `<span name>.<output key>`.
- `exclude_spans`: spans left out of the trajectory, by name glob
  (`"OpenAILike.*"`), attribute (`{"attribute": K, "equals": V}`) or
  instrumentation scope (`{"scope": "mcp"}`).
- `rekind_spans`, `main_agent`, `subagent_spans`: change a span's kind, pin the
  main agent, or keep sibling agents apart. See
  `references/span-mapping-schema.md`.

A span the agent's mapping cannot read is not an error: it comes out as kind
`other` with every attribute listed as unmapped, and the trajectory simply
lacks it. That is what a wrong mapping looks like in the check.

## Steps

1. **Identify the instrumentation.** Grep the repository for `openinference`
   and `gen_ai.`. Note which one applies, or neither. On the import path
   without the agent's repository, start from `openinference.json` (Phoenix's
   own instrumentation) and correct it in step 6 from the imported spans. Then make sure the
   instrumentation records message content: without it evalkit gets token
   counts but no messages, tool arguments or results, and the trajectory is
   close to empty. The GenAI conventions turn content capture off by
   default; the usual switches are:

   | Instrumentation | Switch |
   | -- | -- |
   | OpenTelemetry GenAI instrumentations (Python `opentelemetry-instrumentation-openai-v2`, Google GenAI, Vertex AI, …) | `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` |
   | OpenInference | on by default; check `OPENINFERENCE_HIDE_INPUTS` / `OPENINFERENCE_HIDE_OUTPUTS` (and the `_MESSAGES` variants) are not `true` |
   | OpenLLMetry (Traceloop) | on by default; check `TRACELOOP_TRACE_CONTENT` is not `false` |
   | Vercel AI SDK | `experimental_telemetry: { isEnabled: true }`; `recordInputs` / `recordOutputs` default to on |

   On the import path, the spans already exist, so step 5 checks them for
   message content (the `llm` line). If they have none, the import still
   works but the trajectories are close to empty; turning capture on now only
   helps spans recorded from then on.

   On the export path, turn it on wherever spans are exported to evalkit: the process that runs
   the eval, and any deployed agent or worker those runs call. Whether a
   deployed agent also exports its app traffic (runs outside any experiment:
   production, staging, or by hand) is the developer's choice; ask them.
   evalkit shows app sessions on the Runs page too, and `evalkit runs pull
   <agent> --source app` downloads them. Exporting app traffic is also what
   lets the eval-driven-development skill mine new cases from real runs
   (its Loop 2).
   To export only eval runs, see "Choosing what a shared worker exports"
   below.
2. **Create the agent with a mapping.** Copy the framework's file (or the
   generic starting point from step 1) into the repo and create the agent with it. Add `--harness-url`
   when the agent also has a Harness API; leave it out for a code-driven
   agent.

   ```bash
   cp <this skill>/references/openinference.json eval/span-mapping.json
   evalkit agents create <agent> --trajectory-source otel --mapping eval/span-mapping.json
   ```

   The agent must exist before any span arrives: the collector refuses spans
   for an unknown agent, and neither a token nor the first export creates
   one.
3. **Export path: register the exporter.** On the import path, skip this
   step: the agent's code does not change.

   *After* the client's own tracer provider is set
   up, add

   ```python
   from evalkit.sdk import tracing

   tracing.register(agent="<agent name>")
   ```

   `register` adds an exporter to the provider that exists; placed before the
   client's setup it creates a second provider and the agent's spans never
   reach it. It needs the `otel` extra (`uv add "evalkit-py[otel]==<server version>"`).
   When the client builds its own pipeline and should not touch the global
   provider, use the parts `register` is made of:
   `tracing.processor(agent=...)` returns evalkit's batch processor (it also
   stamps the run's `session.id` and `evalkit.*` attributes),
   `tracing.exporter(agent=...)` the OTLP HTTP exporter inside it, and
   `tracing.headers(agent=...)` its headers. All three read the server URL
   like `register` does and require `EVALKIT_OTEL_TOKEN`.
   `register` only sees spans that pass through the provider's span
   processors. Code that hands spans straight to one processor or exporter
   (a bridge that converts another tool's spans, for example) bypasses it;
   give that code `tracing.processor(...)` as well, or one
   `SynchronousMultiSpanProcessor` that holds every processor, evalkit's
   included. Such spans are already finished, so the processor cannot stamp
   them: the bridge copies `session.id` onto each span it converts.
   A non-Python agent, or any exporter outside the process that calls
   `run_experiment`, sets
   `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<server>/api/otel/v1/traces` and
   `OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <token>,X-Evalkit-Agent=<agent>"`
   instead, with an agent token from `evalkit tokens create --agent <agent>`.
   The login token expires and a long-running worker then loses every span
   without an error of its own. One process that serves several agents can
   name the agent per span or per resource with the `evalkit.agent`
   attribute; the header is the default for spans that carry none.
   LLM spans that carry the full input of every call (OpenInference and
   GenAI instrumentations do) are large, so also set
   `OTEL_BSP_MAX_EXPORT_BATCH_SIZE=16` and `OTEL_EXPORTER_OTLP_TIMEOUT=60`
   on every process that exports (seconds in Python; other SDKs read
   milliseconds, `60000`). The server refuses a batch over
   `otel.max_batch_bytes` (16 MiB by default) with `413` and the spans are
   lost, and the default 10 s timeout is too short for multi-MB spans.
4. **Get one session into evalkit.**

   - **Export path.** `run_experiment(..., dry_run=True)` runs one case,
     creates no experiment, and prints the session id (`session <id>: N spans
     ...`). The agent's own entry point works too; the Runs page lists the
     session either way.
   - **Import path.** Import the session the developer named, with the
     trace source from `evalkit trace-sources list`:

     ```bash
     evalkit traces import <agent> <session id> --source <trace source> --project <Phoenix project>
     ```

     It prints `imported <n> spans`, waits the server's settle time (a pause
     here is normal), then prints the same lines as step 5's check.
     `imported 0 spans` means the project has no spans for that id: check the
     project name and the id. You can also import from the agent's Import tab
     in the web UI.

     If the project's Sessions tab is empty, its spans carry no `session.id`
     and there is no id to give. Import a short range instead (an hour with
     traffic, see step 8; the mapping from step 2 is already active, so the
     range is allowed). With a mapping whose `session_id` rule reads
     `session.id`, it prints `no sessions were imported`: that is expected
     here, not a failure. The spans are in evalkit now. Find one trace id
     on the agent's Runs page with the "no session" box ticked (the row's id
     is the trace id), and read its root span's attributes with
     `evalkit traces check <agent> <trace id> --dump spans.json`.

     - If the root span carries an attribute that names the run or the
       conversation, point the mapping's `session_id` rule at it (steps 6–7)
       and import the same hour again. The root span is enough: every span
       of a trace takes its session from the trace's root.
     - If it carries none, the import path cannot group these spans into
       runs. The developer adds `session.id` to the agent's root span, which
       only helps traces recorded from then on, or takes the export path.
5. **Check it.**

   ```bash
   evalkit traces check <agent> <session id> --dump spans.json
   ```

   Read the lines against this table. `spans.json` holds the raw attributes.
   On the import path the import already printed these lines; run the check
   only for `spans.json`.

   | Line | What it tells you |
   | -- | -- |
   | `session` | span count, trace count, settled or not |
   | `root` | the root span's name and kind; missing root means the session id is not on the root span |
   | `mapping` | the active version, `vN` |
   | `llm` | spans read as `llm`, the model, token totals; `no llm spans` means the kind rule does not match; `without messages` means those spans carry no messages, usually because message content capture is off; `attributes dropped by the OTel SDK` means those spans passed the SDK's 128-attribute limit and lost messages or parts of them, so set `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT=4096` in the agent's environment |
   | `tools` | spans read as `tool`, and how many carry a `tool_call_id` that matches a call the LLM spans emitted; a gap means tool spans exist but their call ids are not read, so results only match by time and order; `in no step` names tool spans that started before any LLM span and so are not in the trajectory |
   | `unmapped` | attributes no rule read, apart from the mapping's `known_attributes` |
   | `trajectory` | ATIF steps, tool calls, subagents |
   | `steps` | shown only when there are more LLM spans than LLM steps; check `exclude_spans` and the kind rule |
   | `subagents` | shown only when subagents hold every LLM call and the main agent none; usually an outer wrapper span; set `main_agent` or `exclude_spans` |
   | `answer` | shown only when the mapping's `answer` rule names a span the session does not have; fix the name (a copied placeholder such as `<agent name>`, or a typo) |

   Each line of the check output starts with `✓` (fine), `!` (a warning) or
   `✗` (a failure). Read every `!` line, even when the check exits 0: each
   one names a trajectory that is likely wrong, such as tool spans with no
   tool calls in the trajectory, or subagents holding all the LLM calls while
   the main agent has none.

   An AGENT span (`openinference.span.kind=AGENT` or
   `gen_ai.operation.name=invoke_agent`) becomes a subagent named by its
   `agent.name` (`gen_ai.agent.name`) only when it sits under a tool span or
   carries the `tool_call_id` that started it. Every other AGENT span (an
   outer wrapper, graph nodes tagged AGENT, one root span per chat turn) is
   merged into the main agent. When that is wrong for the agent, set
   `main_agent` or `subagent_spans` in the mapping; drop duplicate spans with
   `exclude_spans` and fix a wrong kind with `rekind_spans`.

   If the check fails with `413` and names `otel.max_session_bytes`, the
   session holds more span attributes than the server will build into a
   trajectory (384 MiB by default). The limit counts what a build loads: the
   input messages of the first LLM call under each root span and in each subagent, and the output, tool calls
   and tokens of every LLM call, not the input of later calls. Check a shorter session, and tell the
   developer: the deployment's admin can raise the limit together with the
   server's memory.

   On the import path, also open `spans.json` and confirm the root span
   carries the attribute the mapping's `session_id` rule names. The
   one-session import falls back to the id you gave when the rule finds
   nothing, so the lines look right even with a wrong rule; the range import
   in step 8 has no fallback. Phoenix groups sessions by `session.id`, while
   `gen_ai.json`, `google-adk.json`, `mastra.json` and `claude-agent-sdk.json`
   read `gen_ai.conversation.id`.

   When the lines look right, go to step 7.
6. **Fix the mapping** when they do not. Edit `eval/span-mapping.json` (it is
   JSON, one rule per ESF field) and change the attribute keys to the ones in
   `spans.json`.
   `references/span-mapping-schema.md` explains every rule shape and the
   three message formats. Push it as a draft and check with it:

   ```bash
   evalkit traces mapping push <agent> eval/span-mapping.json
   evalkit traces check <agent> <session id> --mapping draft
   ```

   Repeat until the unmapped list holds nothing that matters and the counts
   are right. A push replaces the previous draft and gets the next version;
   pushing the same document again keeps the draft's version.
7. **Activation needs the developer's approval.** If the developer has
   approved the document in this conversation, run
   `evalkit traces mapping activate <agent> <version>` with the version
   `mapping push` printed for the draft you checked.
   Otherwise tell them to review it on the agent's Mappings tab (Agents →
   the agent → Mappings in the web UI) and activate it there. Green check
   output is not approval; a mapping applies to every future run of the agent.

   On the export path you are done. On the import path, go on to step 8.
8. **Import path: import the history.** Before importing, tell the developer
   two things and get a yes, as for activation in step 7:

   - **Who can read it.** Imported runs land in the agent's workspace, and
     every user on the evalkit server can read every workspace. A history
     of app traffic may hold end users' data.
   - **How much.** Ask how far back they want. Each span is stored on the
     evalkit server, and one long session can take hundreds of MB, so a
     large range takes time and storage.

   A time range imports every trace that started in it, as a job on the
   server. Import one day first:

   ```bash
   evalkit traces import <agent> --source <trace source> --project <Phoenix project> --since <yesterday> --until <today>
   ```

   For example `--since 2026-11-10 --until 2026-11-11`, with dates
   worked out from today. `--since` and `--until` take ISO 8601 times, UTC unless the value carries
   an offset; `--until` is exclusive and defaults to now. The CLI follows
   the job's progress, and Ctrl-C only stops following. When the job is done
   it prints the check lines for one sample session. If they look right,
   import the full range the developer chose.

   - The server groups the spans into sessions by the attribute the active
     mapping's `session_id` rule names. Without a `session_id` rule it refuses
     the range with `409 activate a mapping with session_id before importing a
     time range`.
   - `no sessions were imported` has two causes; read the last progress
     line (`... N traces M spans`) to tell them apart. `0 traces` means the
     range held no traffic: widen it. Some traces and no sessions means the
     rule names an attribute the spans do not carry. Fix the rule, push and activate it
     (steps 6–7), and import the same range again: a re-import rewrites each
     span's session.
   - `evalkit traces imports <agent>` lists the agent's range imports;
     `evalkit traces imports <agent> <id>` follows one, `--stop` stops it and
     `--continue` resumes a failed or stopped one from where it got to.
   - Importing the same range again duplicates nothing.

   The imported sessions are app runs: the developer finds them on the
   agent's Runs page and downloads them with
   `evalkit runs pull <agent> --source app`.

## An agent in another process

`tracing.register` stamps `session.id` and the `evalkit.*` attributes only on
spans made in the process that calls `run_experiment`. An agent behind HTTP
or a job queue, or written in another language, joins the run in one of two
ways. Pick one, not both.

- **Continue the trace.** The task injects the W3C trace context into the
  request (`opentelemetry.propagate.inject(headers)`) and the agent starts its
  spans from it (`traceparent`). Every span in a trace belongs to the session
  of the trace's root, the SDK's `evalkit.run` span, even when the agent
  stamps its own `session.id` or `gen_ai.conversation.id`. The task returns
  only the answer.
- **Return the agent's session.** The agent puts its own session id on its
  root span as `session.id`, and the task returns
  `RunResult(answer=..., session_id=<that id>)`. Use this when the trace
  context cannot reach the agent, for example through a queue. The SDK moves
  its `evalkit.run` span into that session so no empty session is left
  behind, and the server builds the trajectory from the agent's trace.

Either way the run's row on the Runs page links the session to its case.

A queue-backed agent usually opens its session in the worker, after the API
call has returned. Two ways to get the session id to evalkit, no new field
needed on either path:

- **Code-driven (`run_experiment`).** The task returns
  `RunResult(session_id=...)` with whatever id the agent's own API returns.
  If the API cannot return one, the task passes `metadata["session_id"]`
  (the SDK's own session id) along with the job, and the agent uses it as
  its session id; the task then returns only the answer.
- **Harness API with an `otel` trajectory source.** The session evalkit polls is the `run_id`
  that `POST /runs` returns. Return the agent's own session id as `run_id`,
  or stamp the returned `run_id` on the job's spans as `session.id`.

## Choosing what a shared worker exports

A worker that serves app and eval runs exports every span it makes, so each
app turn also becomes a session on the evalkit server, where
the developer can browse it on the Runs page. That is a valid setup; ask the
developer whether they want it. evalkit provides the stamping to tell eval
runs apart either way. To export only the eval runs:

```python
from evalkit.sdk import tracing

provider.add_span_processor(tracing.processor(agent="<agent>", only=tracing.is_eval_span))


def handle_job(job):
    if job.evalkit:  # the task's metadata, sent with the job
        with tracing.eval_run(job.evalkit):
            return run_agent(job)
    return run_agent(job)
```

`eval_run` stamps `session.id` (the SDK's session id) and the `evalkit.*`
attributes on every span started inside it, so those spans also join the
run's session. `is_eval_span` is true for a span that carries
`evalkit.case_id`. Spans started in another thread need that thread inside
`eval_run` too. Do not use a sampler: it drops spans for every exporter, the
client's own backend included.

A harness agent learns that a run is an eval run from the `metadata` of
`POST /runs` (`experiment_id`, `case_id`, `repetition`). That is what it
passes to `eval_run`, with the `run_id` it returns as the session id:
`tracing.eval_run({**metadata, "session_id": run_id})`.

`only` decides by trace. It runs on each span as it starts and as it ends;
once it is true for one span, the processor keeps every span of that trace
that reaches it, including finished spans a bridge hands straight to
`processor.on_end` and the job's spans started before `eval_run`. Other
traces are dropped. A custom `only` picks whole traces the same way.

- **Order.** A span is kept when its trace is already marked as the span
  ends. The first span started inside `eval_run` marks the trace as it
  starts, so spans that end before it and spans bridged later are kept. A
  span that ends before any span of its trace was marked is dropped; a
  bridge that may hand spans over that early can copy the `evalkit.*`
  attributes onto them so each marks its own trace.
- **Bridges.** Still copy `session.id` onto every converted span: the server
  takes a trace's session from its root span when the root reaches it, and
  from each span's own `session.id` otherwise.
- **Memory.** The processor remembers the 10,000 most recently used eval
  traces and forgets the oldest.
- **Other processes.** Spans from another process in the same trace (an MCP
  gateway pod, for example) never reach this processor; they go through that
  process's own exporter. That process makes its own choice: continue the
  trace, wrap its work in `eval_run` with the run's metadata sent along with
  the request and filter with `only=tracing.is_eval_span`, or export
  everything. Ask the developer which.

A worker in another language does the same with its own processor: forward
a span to the evalkit exporter only when its trace has a span carrying an
attribute the worker set on eval jobs.

## Capabilities of a code-driven agent

A code-driven agent has no capabilities endpoint, so evalkit records
`thinking: false` (and `artifacts`, `workspace` false) for it and never reads
capabilities from spans. When the agent's spans carry the model's reasoning,
set it on the agent:

```bash
evalkit agents edit <agent> --provides thinking=true
```

Declare only what works; evaluators gated on a flag trust it.

If the agent's spans cannot be expressed with the rule shapes in the schema
reference, open an evalkit issue rather than working around it.

For bugs, feature requests and feedback on evalkit, open an issue at https://github.com/inferedge-ai/evalkit/issues; questions go to https://github.com/inferedge-ai/evalkit/discussions.
