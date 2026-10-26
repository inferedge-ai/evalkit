# SDK runs

`run_experiment` runs an experiment from your own code: it calls your task once per case and repetition, stores the runs on this server, and scores them with the judge, the remote evaluators of the agent's workspace and any Python evaluators you pass. The agent is a code-driven agent on the server (no harness URL), created once with `evalkit agents create`. Invoking the agent, timeouts and stopping are your code.

```python
# eval/run.py — uv run python eval/run.py
from evalkit.sdk import run_experiment, RunResult, evaluator, tracing
from myagent import MyAgent, setup_tracing

setup_tracing()                          # your own OpenTelemetry provider and instrumentors
tracing.register(agent="myagent-sdk")   # add this server as an export destination
agent = MyAgent()

def task(input: dict, files: list) -> str:
    return agent.ask(input["question"], attachments=[f.path for f in files])

@evaluator(name="citation_check")
def citation_check(case, run) -> float:
    cited = {c["url"] for c in (run.output or {}).get("citations", [])}
    return 1.0 if cited >= set(case.config["required"]) else 0.0

if __name__ == "__main__":
    result = run_experiment(dataset="finance", agent="myagent-sdk", task=task,
                            evaluators=[citation_check], concurrency=4)
    print(result.url)
```

## The task

A sync or async callable. Parameters bind **by name** from this set, so their order does not matter; any other required parameter is an error before the first call, and an optional one keeps its default.

| Parameter  | Value                                                                 |
| ---------- | --------------------------------------------------------------------- |
| `input`    | the case input, verbatim, minus `files`                               |
| `files`    | `list[Fixture]`, the case's fixture files, downloaded before the call |
| `model`    | the `model` argument of `run_experiment`, or `None`                   |
| `metadata` | a dict with the keys below                                            |

A `Fixture` has:

- `name`: the fixture key the case names.
- `path`: where the file sits on this machine.
- `context`: the file's description from the fixture registry, or `None`. A Harness API agent gets the same text with each file; show it to your agent the same way (for example an "Attached files" block ahead of the question).

`metadata` holds:

- `session_id`: the SDK's session id for this run, the `session.id` of its `evalkit.run` span.
- `experiment_id`: the experiment, or `None` on a dry run.
- `case_id`: the case.
- `repetition`: 1 for the first repetition, 2 for the second, and so on.
- `tags`: the case's tags.

`model` is the only way `run_experiment(model=...)` reaches your agent. The experiment records the requested model either way, so a task without a `model` parameter would silently run the agent's default; `run_experiment` prints a warning before the first call when that happens.

A task with exactly one parameter whose name is not in the table (`def task(q)`) receives the input. Return a `str` (the answer), a `dict` (the structured output) or a `RunResult`:

```python
from evalkit.sdk import RunResult

RunResult(answer="…", output={"citations": []}, model="gpt-5",
          session_id="…",          # override the session the SDK polls
          trajectory={...})        # push an ATIF document instead of tracing
```

A task that raises records the run as `failed` with the exception and the experiment continues.

## An agent in another process

The SDK stamps `session.id` and the `evalkit.*` attributes only on spans made in this Python process. An agent behind HTTP or a job queue, or written in another language, joins the run in one of two ways. Pick one, not both.

- **Continue the trace.** Inject the W3C trace context into the request and have the agent start its spans from it. Every span in a trace belongs to the session of the trace's root, the SDK's `evalkit.run` span, even if the agent stamps its own `session.id` or `gen_ai.conversation.id`.

  ```python
  from opentelemetry import propagate

  def task(input: dict) -> str:
      headers: dict[str, str] = {}
      propagate.inject(headers)            # adds traceparent
      return httpx.post(AGENT_URL, json=input, headers=headers).json()["answer"]
  ```

- **Return the agent's session.** The agent puts its own session id on its root span as `session.id`, and the task returns it. Use this when the trace context cannot reach the agent, for example through a queue. The SDK moves its `evalkit.run` span into that session, so no empty session is left behind, and the trajectory is built from the agent's trace.

  ```python
  def task(input: dict) -> RunResult:
      job = agent_api.start(input["question"])           # your API returns the session it opened
      return RunResult(answer=job.wait(), session_id=job.session_id)
  ```

  If your API cannot tell you the session id, send it one instead: pass `metadata["session_id"]` along with the job and have the agent use it as its session id (or wrap the job in `tracing.eval_run(metadata)`, see [Tracing](tracing.md)). Its spans then join the SDK's session and the task returns only the answer.

  ```python
  def task(input: dict, metadata: dict) -> str:
      return queue.submit(input["question"], session_id=metadata["session_id"]).wait()
  ```

Either way the Runs page links the session to its experiment and case.

The agent's exporter needs its own token. Mint one with `evalkit tokens create --agent <name>`; it never expires. Your login token expires, and an exporter that holds it then drops every span with nothing but a line in its own log.

## Datasets and workspaces

- `dataset="finance"` runs your own workspace's copy; if `finance` is a directory under `cases` it is synced first.
- `dataset="finance@dev"` or `dataset="finance", workspace="dev"` runs the shared cases (Approved ones only, unless `include_drafts`).
- `case_ids=[...]`, `split="..."`, `repetitions`, `model`, `resume=<experiment id>` and `include_drafts` mean what the same options mean on `evalkit run`.
- `dry_run=True` runs one case (`dry_run=N` runs N) without creating an experiment and prints the answer and what the server made of the session. If the server cannot read a session, that case prints a `trajectory read failed` line and the dry run goes on. Use it while wiring up tracing; the session shows on the Runs page with its case question and case id.

## Notebooks

`run_experiment` refuses to run inside a running event loop. In a notebook use the async form:

```python
from evalkit.sdk import arun_experiment

result = await arun_experiment(dataset="finance", agent="myagent-sdk", task=task)
```

## The agent and the judge

`agent="myagent-sdk"` names an agent on the server; a bare name means your own workspace. Create it once:

```bash
evalkit agents create myagent-sdk --trajectory-source otel --mapping openinference.json \
  --max-concurrent-runs 4 --provides thinking=true
```

Its `answer` rule and `exclude_spans` live in its span mapping. `evalkit.yaml` is still required; it holds the cases, fixtures and credentials.

The judge is the server's default unless you pass one:

```python
from evalkit.sdk import Judge, run_experiment

run_experiment(dataset="finance", agent="myagent-sdk", task=task,
               judge=Judge(base_url="https://litellm.example.com", model="gpt-5"))
```

## Capabilities

A code-driven agent has no `GET /capabilities`, so evalkit writes its capabilities onto each experiment itself: `trajectory`, `tool_calls`, `token_usage` and `run_listing` are true; `thinking`, `artifacts` and `workspace` are false. Evaluators gated on a capability skip when it is false. evalkit does not read capabilities from the spans. Set `provides` on the agent (`evalkit agents create --provides`, or `evalkit agents edit`) to declare what it really does, for example `thinking: true` when its spans carry the model's reasoning. Declare only what works: a false `true` produces failing scores blamed on the agent.

## Python evaluators

`@evaluator(name=..., kind="evaluator" | "gauge", requires_trajectory=False)` turns a function into an evaluator. It receives the same `EvalCase` and `EvalRun` the HTTP Custom Evaluator API sends: the case id, input, expected block and this evaluator's config from the case YAML; the answer, structured output and the ATIF trajectory as a dict. Return a float, a `ScoreItem`, or a list of them. A case selects it by name under `evaluators.custom`, exactly like an HTTP evaluator.

## Bring your own benchmark

A published benchmark is a suite plus a Python evaluator:

1. Write a script that turns the benchmark's file into case YAML. Each case gets the question as `input`. Its grading data (rubrics, reference answer) goes under `evaluators.custom.<grader name>`.
2. `evalkit sync <dir>` puts it in a dataset like any suite.
3. Write the grader as an `@evaluator` with that name. It reads the grading data from `case.config`, and calls its own judge models if it needs any. Pass it to `run_experiment`.

`evalkit lint` and `evalkit sync` warn with `W-CUSTOM-UNKNOWN` for the grader name, because the server can't see a Python evaluator. That warning is expected. In a shared workspace the synced cases stay Draft until reviewed, so pass `include_drafts=True` to run them unreviewed.

## Re-scoring

Python evaluators live in your script, so a re-score that needs them starts from the script:

```python
from evalkit.sdk import evaluate_experiment

evaluate_experiment(experiment_id="…", evaluators=[citation_check],
                    refetch_trajectories=True)
```

`refetch_trajectories` polls the collector again for runs whose trajectory was `partial` or `unavailable` and re-scores them. `evalkit evaluate` refuses experiments that used Python evaluators and points here.

Next: [Tracing](tracing.md).
