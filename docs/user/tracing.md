# Tracing

The collector is an OTLP/HTTP endpoint on this server:

```
POST {{origin}}/api/otel/v1/traces
```

It accepts `application/x-protobuf` and `application/json`, gzip or not, from any OpenTelemetry SDK. Spans are stored as received and converted to ATIF with the agent's mapping when a run or the Runs page asks for them.

## From Python with the SDK

```python
from evalkit.sdk import tracing

tracing.register(agent="myagent-sdk")
```

`register` adds an exporter for this server to your global tracer provider, creating one if none is set. Your existing exporters keep running. The exporter carries an `X-Evalkit-Agent` header, which names the agent on the server; the `evalkit.agent` resource attribute is only set when `register` creates the provider itself. It reads the server URL from `EVALKIT_SERVER_URL` or your `evalkit login`, and the token from `EVALKIT_OTEL_TOKEN` (an agent token), then `EVALKIT_TOKEN`, then your `evalkit login`. It needs the `otel` extra.

### An existing span pipeline

`register` changes the global provider. When you build your own pipeline, take evalkit's parts and put them where you need them:

```python
from evalkit.sdk import tracing

exporter = tracing.exporter(agent="myagent-sdk")    # OTLPSpanExporter: URL, token and X-Evalkit-Agent set
processor = tracing.processor(agent="myagent-sdk")  # batch processor around that exporter
provider.add_span_processor(processor)              # what register does for you
headers = tracing.headers(agent="myagent-sdk")      # the same headers, for an exporter you build yourself
```

All three resolve the server and token the same way `register` does. `processor` also stamps the run's `session.id` and `evalkit.*` attributes on spans that start under a task call (or under `tracing.eval_run`, below).

Spans handed straight to a processor or exporter, for example by a bridge that converts another tool's spans, never pass through the provider, so `register` cannot see them. Hand them to `tracing.processor(...)` too: give the bridge that processor, or one processor that fans out to all of yours and evalkit's (OpenTelemetry's `SynchronousMultiSpanProcessor`). Those spans are already finished, so `processor` cannot stamp them: the bridge copies `session.id` onto each span it converts. How eval-only export treats them is below.

### Choosing what a shared worker exports

A worker that serves both app runs (production, staging or manual use) and eval runs exports every span it makes, so every app turn also becomes a session on this server, which you can browse on the Runs page and pull with `evalkit runs pull <agent> --source app`. Whether to send app traffic is your choice. `eval_run` marks eval runs either way, and to send only those, filter on the mark:

```python
from evalkit.sdk import tracing

provider.add_span_processor(tracing.processor(agent="myagent", only=tracing.is_eval_span))

def handle_job(job):
    if job.evalkit:                           # the task's metadata, sent along with the job
        with tracing.eval_run(job.evalkit):
            return run_agent(job)
    return run_agent(job)
```

The task passes its `metadata` argument along with the job (see [SDK runs](sdk-runs.md)). `eval_run` stamps `session.id` (the SDK's session id), `evalkit.case_id`, `evalkit.experiment_id` and `evalkit.repetition` on every span started inside it, so the worker's spans join the run's session and the task returns only the answer. `is_eval_span` is true for a span that carries `evalkit.case_id`. The stamp lives in a context variable: spans started in another thread need that thread to run inside `eval_run` too.

A harness agent learns that a run is an eval run from the `metadata` of `POST /runs`: `experiment_id`, `case_id` and `repetition`. That is what it passes to `eval_run`, with the `run_id` it returns as the session id: `tracing.eval_run({**metadata, "session_id": run_id})`.

`only` decides by trace, not by span. It is any function that takes a span and returns a bool, and it runs on each span as the span starts and again as it ends. Once it is true for one span, the processor keeps every span of that span's trace that reaches it: spans started inside `eval_run`, the job's own spans started before `eval_run` in the same trace, and finished spans a bridge hands straight to the processor. Spans of other traces are dropped. A custom `only` picks whole traces the same way.

The rule for order: a span is kept when its trace is already marked by the time the span ends. The first span started inside `eval_run` marks its trace as it starts, before anything under it ends, so spans that end before it and spans a bridge hands over later are both kept. A span that ends before any span of its trace was marked is dropped. A bridge that may hand spans over that early can copy the `evalkit.*` attributes onto them, so each one marks its own trace. A bridge should copy `session.id` either way: the server takes a trace's session from its root span when the root reaches it, and from each span's own `session.id` otherwise.

The processor remembers the 10,000 most recently used eval traces and forgets the oldest. Each kept span refreshes its trace, so a trace is only forgotten once 10,000 other eval traces have been used since its last span.

Spans from another process in the same trace, such as an MCP gateway pod the agent calls, never reach this processor. They go through that process's own exporter, so that process makes its own choice: either it continues the trace, wraps its work in `eval_run` with the run's metadata sent along with the request, and filters with `only=tracing.is_eval_span` too, or it exports everything.

Do not use a sampler for this: a sampler drops spans for every exporter, your own backend included.

Around every task call the SDK opens a root span named `evalkit.run` with `session.id` set to the run id, and stamps `session.id`, `evalkit.experiment_id`, `evalkit.case_id` and `evalkit.repetition` onto every span your code starts underneath it. That is what groups a trace into one session. When the task returns `RunResult(session_id=...)`, the root span takes that session id instead, so it joins the agent's session rather than sitting alone; see [SDK runs](sdk-runs.md) for agents in another process.

## From any OpenTelemetry SDK

Point the exporter at the collector and name the agent, either with a header or an attribute:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT={{origin}}/api/otel/v1/traces
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer <token>,X-Evalkit-Agent=myagent"
export OTEL_RESOURCE_ATTRIBUTES="evalkit.agent=myagent"   # alternative to the header
```

The collector picks the agent per span: the span's `evalkit.agent` attribute first, then the resource's, then the `X-Evalkit-Agent` header. One process that serves several agents can therefore share one exporter and set `evalkit.agent` on each agent's spans. The value is an agent reference (`myagent` or `myagent@workspace`). Every agent a batch names is checked as if it came in the header: an agent token writes only its own agent, a login token only agents in workspaces you can write to (your own, or a shared one where you are a writer). If any span names an agent the token may not write, the whole batch is refused.

Put the session id on the root span of each trace, in the attribute your mapping's `session_id` rule names (`session.id` in `openinference.json`, `gen_ai.conversation.id` in `gen_ai.json`). Every span in a trace belongs to the session of the trace's root, even one that carries a session attribute of its own. Only when the root has no session do child spans keep theirs.

## Deployed agents

A service cannot carry a person's token. Mint an agent token, bound to one agent and valid only for the collector:

```bash
evalkit tokens create --agent ouragent@dev     # needs writer on dev
evalkit tokens list
evalkit tokens revoke <id>
```

Set it on the deployment as `EVALKIT_OTEL_TOKEN` next to `EVALKIT_SERVER_URL`; `tracing.register`, `tracing.exporter` and `tracing.processor` pick both up. For a non-Python exporter put it in `OTEL_EXPORTER_OTLP_HEADERS`. Use an agent token for any exporter outside the process that calls `run_experiment` too, even on your laptop: the login token expires under a long-running worker.

The collector logs every refused export as `otel export rejected` with the status, the reason (`expired token`, `invalid token`, `unknown agent`, `forbidden`, `too large`, `malformed`) and the agent. OpenTelemetry exporters only log a failed export on their side, so a silent agent is worth checking in both logs.

## Size limits and large spans

LLM spans from OpenInference and GenAI instrumentations carry the full input of each call: the messages so far, the system prompt and the tool schemas. Every turn repeats the whole context, so a long agent session grows with roughly the square of its turns. A 14-minute chat with subagents can store hundreds of MB of span attributes.

The server has two limits, both set in its config:

| Limit                    | Default | What happens past it                                                                                                                                                                                                                      |
| ------------------------ | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `otel.max_batch_bytes`   | 16 MiB  | One export request (after gzip is undone) gets `413` and its spans are dropped. Exporters do not split a refused batch, so keep batches small.                                                                                            |
| `otel.max_session_bytes` | 384 MiB | Reading the session (a run's trajectory, `evalkit traces check`, the Mappings tab) gets `413` with the session's size and the limit. A run records the trajectory as `unavailable` and logs the message. `--dump spans.json` still works. |

`otel.max_session_bytes` counts the span attributes a trajectory build loads, not everything stored. evalkit reads the input messages (and system prompt) of an LLM span only for the first LLM call of each agent and subagent; later calls add only their output, tool calls and tokens to the trajectory. So the build leaves the input of every later LLM call in the database and does not count it. The limit keeps one session from using up the server's memory, so raise it only together with the server's memory limit.

For agents with large spans, send fewer spans per export and give each export more time. For the Python SDK, `tracing.register` included:

```bash
export OTEL_BSP_MAX_EXPORT_BATCH_SIZE=16   # spans per export; default 512
export OTEL_BSP_MAX_QUEUE_SIZE=2048        # spans waiting for export; raise it if the exporter logs "Queue full"
export OTEL_EXPORTER_OTLP_TIMEOUT=60       # seconds per export in Python; default 10
```

Other SDKs follow the OpenTelemetry spec and read `OTEL_EXPORTER_OTLP_TIMEOUT` in milliseconds (`60000`). A 10 MB span can take more than 10 s to upload, longer than the default timeout. In an OpenTelemetry Collector, set `send_batch_max_size` on the `batch` processor and `timeout` on the `otlphttp` exporter instead.

## Exporting to evalkit and another backend

The standard OTLP variables name one endpoint. To keep exporting to your tracing backend and send the same spans here, either:

- add a second `otlphttp` exporter to the OpenTelemetry Collector the agent already exports to (recommended for deployed agents), or
- add a second span processor and exporter in code. `tracing.register` does this for Python.

```yaml
exporters:
  otlphttp/evalkit:
    traces_endpoint: {{origin}}/api/otel/v1/traces
    headers:
      Authorization: Bearer ${env:EVALKIT_OTEL_TOKEN}
      X-Evalkit-Agent: ouragent@dev
service:
  pipelines:
    traces:
      exporters: [otlp/existing, otlphttp/evalkit]
```

The collector speaks OTLP over HTTP only, not gRPC. Bodies may be chunked with no `Content-Length` (the JavaScript exporter sends them that way); a proxy in front of the collector must allow that.

## The main agent and subagents

Mark the agent's top-level span as an AGENT span (`openinference.span.kind=AGENT`, or `gen_ai.operation.name=invoke_agent`). evalkit builds the main trajectory from it, and every AGENT span inside it becomes a subagent trajectory named by the span's `agent.name` (`gen_ai.agent.name`), or by the span name when that is missing. When the root span is not an AGENT span, as with the SDK's `evalkit.run` span, evalkit takes the earliest AGENT span that is not inside another one as the main agent. If that span is a tool call that starts a subagent partway through the run, the subagent's steps land in the main trajectory and the call has no result, so mark the top-level span as an AGENT span rather than relying on this.

## Harness agents with traces

A harness agent created with `--trajectory-source otel` keeps the whole Harness API flow and skips `GET /runs/{id}/trajectory`: it puts the run id it returned on its spans, in the attribute its mapping's `session_id` rule names, and evalkit polls the session until it settles. The harness declares `provides.trajectory: false`; evalkit still runs the trajectory evaluators because the trajectory comes from the collector.

The session evalkit polls is the `run_id` that `POST /runs` returns, so there is no separate session field. A queue-backed agent whose worker picks its own session id returns that id as `run_id`, or puts the `run_id` it returned on its spans in that session attribute. To export only eval runs from a harness that also serves production, see Choosing what a shared worker exports above.

## Importing history from a trace source

An admin registers a trace source (a Phoenix server) once. After that, any
user can import traces from it into an OTel agent they can write, with
`evalkit traces import` or the agent page's Import tab.

- The trace source's API key is shared with every user in effect: anyone can
  import any project that key can read.
- Imported traces land in the agent's workspace. Every user can read every
  workspace, so traces imported into your personal workspace are visible to
  everyone on the server. See [Where runs happen](workspaces.md).

## Checking a session

```bash
evalkit traces check myagent-sdk <session id>
```

prints the settle state, span kinds, tokens, the attributes the mapping did not read and the ATIF summary. `--dump spans.json` writes the raw spans. `--dump esf.json` leaves out the input messages of every LLM call after each agent's first, as the trajectory does not use them; `spans.json` has the raw values. A session over `otel.max_session_bytes` fails the check with its size and the limit (see Size limits and large spans above). If the kinds or messages look wrong, the agent needs a mapping: see [Mappings](mappings.md).
