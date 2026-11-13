# SDK reference

The Python SDK runs experiments from your own code. Install `evalkit-py` at your server's version (see [Install](install.md)) and import from `evalkit.sdk`. [SDK runs](sdk-runs.md) walks through it.

## Running experiments

### run_experiment

```python
def run_experiment(
    *,
    dataset: str,
    agent: str,
    task: Callable[..., Any],
    workspace: str | None = None,
    evaluators: Sequence[PythonEvaluator] = (),
    repetitions: int = 1,
    concurrency: int | None = None,
    case_ids: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    model: str | None = None,
    resume: int | None = None,
    dry_run: bool | int = False,
    trajectory_wait_seconds: int | None = None,
    judge: Judge | None = None,
    evaluation_concurrency: int = 16,
) -> ExperimentResult:
```

Run an experiment: call `task` once per case and repetition, store the runs on
the server and score them.

Scoring uses the judge, the remote evaluators of the agent's workspace and the
Python `evaluators` you pass. Reads `evalkit.yaml` (cases, fixtures, credentials)
from the current directory or a parent, and the login from `evalkit login`. The
file is optional: without it, cases are read from `evalkit/cases/<dataset>/` and
fixtures from `evalkit/fixtures/` under the current directory, with no credentials.
Prints the scorecard, then `link: <url>`, the experiment's page in the web UI.

**Args**

- `dataset` (`str`): The dataset to run. A bare name means your personal workspace; if it is also a directory under the cases root (`cases` in `evalkit.yaml`, `evalkit/cases` by default), it is synced first. `name@workspace` runs a shared dataset, every review state included.
- `agent` (`str`): The code-driven agent on the server that the runs belong to, created once with `evalkit agents create`. A bare name means your personal workspace.
- `task` (`Callable[..., Any]`): Your function, called once per case and repetition, sync or async. Its parameters bind by name from `input` (the case input), `files` (`list[Fixture]`), `model` and `metadata` (`session_id`, `experiment_id`, `case_id`, `repetition`, `tags`). A task with one parameter of another name receives the input. Return a `str` (the answer), a `dict` (the structured output) or a `RunResult`. A task that raises records a failed run.
- `workspace` (`str | None`): The workspace of `dataset` when it is a bare name.
- `evaluators` (`Sequence[PythonEvaluator]`): Python evaluators made with `evaluator`. A case selects one by name under `evaluators.custom`, like an HTTP evaluator.
- `repetitions` (`int`): How many times each case runs.
- `concurrency` (`int | None`): How many tasks run at once. Defaults to the agent's `max_concurrent_runs`.
- `case_ids` (`Sequence[str] | None`): Run only these cases.
- `tags` (`Sequence[str] | None`): Run only the cases carrying any of these tags.
- `model` (`str | None`): Passed to the task's `model` parameter and recorded on the experiment.
- `resume` (`int | None`): An experiment id to resume: runs only the cases it has not finished.
- `dry_run` (`bool | int`): `True` runs one case without creating an experiment and prints the answer and what the server made of the session; an int runs that many.
- `trajectory_wait_seconds` (`int | None`): How long to wait for a run's spans to settle into a trajectory. Defaults to the agent's setting.
- `judge` (`Judge | None`): The judge for LLM-graded built-in evaluators. Defaults to the server's default judge. `Judge(provider=...)` names an LLM provider configured on the server: they run on the server with its stored key. `Judge(base_url=..., api_key=...)` brings your own judge: they run on this machine. The key is never stored.
- `evaluation_concurrency` (`int`): How many runs are scored at once.

**Returns**

- `ExperimentResult`: The experiment id, its URL and one summary per run.

**Raises**

- `RuntimeError`: When called from a running event loop; await `arun_experiment` there instead.

### arun_experiment

```python
async def arun_experiment(
    *,
    dataset: str,
    agent: str,
    task: Callable[..., Any],
    workspace: str | None = None,
    evaluators: Sequence[PythonEvaluator] = (),
    repetitions: int = 1,
    concurrency: int | None = None,
    case_ids: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    model: str | None = None,
    resume: int | None = None,
    dry_run: bool | int = False,
    trajectory_wait_seconds: int | None = None,
    judge: Judge | None = None,
    evaluation_concurrency: int = 16,
    api_transport: httpx2.AsyncBaseTransport | None = None,
    custom_eval_transport: httpx2.AsyncBaseTransport | None = None,
    progress: Callable[[str], None] = print,
    poll_interval: float = 5.0,
    tracer_provider: Any = None,
) -> ExperimentResult:
```

The async form of `run_experiment`, for code already in an event loop (a notebook).

Same arguments and result as `run_experiment`. The arguments after
`evaluation_concurrency` exist for tests.

### evaluate_experiment

```python
def evaluate_experiment(
    *,
    experiment_id: int,
    evaluators: Sequence[PythonEvaluator] = (),
    refetch_trajectories: bool = False,
    evaluation_concurrency: int = 16,
    judge: Judge | None = None,
) -> ExperimentResult:
```

Re-score an experiment that used Python evaluators.

`evalkit evaluate` refuses such experiments, because the evaluators live in your
script; call this from the script instead. Exported as `evaluate_experiment`.
Ends with `link: <url>`, the experiment's page in the web UI.

**Args**

- `experiment_id` (`int`): The experiment to re-score.
- `evaluators` (`Sequence[PythonEvaluator]`): The Python evaluators the experiment ran with. Each must have been part of the experiment.
- `refetch_trajectories` (`bool`): Read the trajectories of runs whose trajectory was `partial` or `unavailable` again before scoring.
- `evaluation_concurrency` (`int`): How many runs are scored at once.
- `judge` (`Judge | None`): The experiment's judge again, for an experiment that brought its own (`Judge(base_url=..., api_key=...)`): keys are never stored, so re-scoring asks for it again. It must be the judge the experiment recorded; a different judge is refused.

**Returns**

- `ExperimentResult`: The experiment id, its URL and one summary per run.

**Raises**

- `RuntimeError`: When called from a running event loop; await `aevaluate_experiment` there instead.

### aevaluate_experiment

```python
async def aevaluate_experiment(
    *,
    experiment_id: int,
    evaluators: Sequence[PythonEvaluator] = (),
    refetch_trajectories: bool = False,
    evaluation_concurrency: int = 16,
    judge: Judge | None = None,
    api_transport: httpx2.AsyncBaseTransport | None = None,
    custom_eval_transport: httpx2.AsyncBaseTransport | None = None,
    progress: Callable[[str], None] = print,
) -> ExperimentResult:
```

The async form of `evaluate_experiment`, for code already in an event loop.

Same arguments and result as `evaluate_experiment`. The arguments after
`judge` exist for tests.

## Tracing

### tracing.register

```python
def register(
    agent: str,
    only: Callable[[Any], bool] | None = None,
) -> None:
```

Send this process's OpenTelemetry spans to the evalkit server.

Adds an exporter to the global tracer provider, or creates one when none is set.
Call it after your own tracing setup, so it joins your provider. Spans go to the
server you logged in to (or `EVALKIT_SERVER_URL`) with the agent token in
`EVALKIT_OTEL_TOKEN` (`evalkit tokens create --agent <agent>`); a person's login
is not accepted, because it expires under a long-running process.

**Args**

- `agent` (`str`): The agent the spans belong to, e.g. `myagent` or `myagent@dev`.
- `only` (`Callable[[Any], bool] | None`): Export only the spans this function returns `True` for.

## Evaluators

### evaluator

```python
def evaluator(
    fn: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    kind: Literal['evaluator', 'gauge'] = 'evaluator',
    requires_trajectory: bool = False,
) -> PythonEvaluator | Callable[[Callable[..., Any]], PythonEvaluator]:
```

Turn a function into a Python evaluator for `run_experiment`.

Use it bare (`@evaluator`) or with options (`@evaluator(name=...)`). The function
receives the same `EvalCase` and `EvalRun` the Custom Evaluator API sends: the
case id, input, expected block and this evaluator's config from the case YAML
(`case.config`); the answer, structured output and ATIF trajectory as a dict.
It returns a float, a `ScoreItem`, or a list of them.

**Args**

- `fn` (`Callable[..., Any] | None`): The function, when used bare.
- `name` (`str | None`): The name cases use under `evaluators.custom`. Defaults to the function's name.
- `kind` (`Literal['evaluator', 'gauge']`): `evaluator` for a score, `gauge` for a measurement.
- `requires_trajectory` (`bool`): Skip runs that have no trajectory.

**Returns**

- `PythonEvaluator | Callable[[Callable[..., Any]], PythonEvaluator]`: The evaluator, or a decorator that makes one.

## Types

### EvalCase

```python
class EvalCase:
    id: str
    input: dict
    expected: dict | None = None
    config: dict
```

The case an evaluator scores.

**Attributes**

- `id` (`str`): Case id.
- `input` (`dict`): Case input, verbatim.
- `expected` (`dict | None`): Case expected block, if any.
- `config` (`dict`): This case's YAML config for this evaluator. Opaque to evalkit — passed through untouched.

### EvalRun

```python
class EvalRun:
    answer: str
    output: dict | None = None
    trajectory: dict
```

The run an evaluator scores.

**Attributes**

- `answer` (`str`): The agent's final answer. When the harness sent only a structured output, this is that output as canonical JSON (sorted keys).
- `output` (`dict | None`): The agent's structured output, parsed, or null when the harness sent none.
- `trajectory` (`dict`): The persisted ATIF trajectory document.

### ScoreItem

```python
class ScoreItem:
    name: str
    score: float | None = None
    label: str | None = None
    explanation: str | None = None
```

One named score an evaluator returns.

**Attributes**

- `name` (`str`): Score name. Multi-score is first-class: one call may return several named scores.
- `score` (`float | None`): null means 'no reading' — recorded but excluded from aggregates.
- `label` (`str | None`): Optional short label.
- `explanation` (`str | None`): Optional. Shown in the UI and EDD bundles.

### RunResult

```python
class RunResult:
    answer: str | None = None
    output: dict[str, Any] | None = None
    model: str | None = None
    error: str | None = None
    session_id: str | None = None
    trajectory: dict[str, Any] | None = None
```

What a task can return instead of a plain answer.

**Attributes**

- `answer` (`str | None`): The agent's answer text.
- `output` (`dict[str, Any] | None`): The agent's structured output.
- `model` (`str | None`): The model the agent used, recorded on the run.
- `error` (`str | None`): An error message. With no answer and no output the run is recorded as failed and gets no score; with one, the run is scored and keeps the message.
- `session_id` (`str | None`): The agent's own session id, when its spans do not continue the SDK's trace. The SDK reads the trajectory from that session.
- `trajectory` (`dict[str, Any] | None`): An ATIF document to store as the trajectory, instead of building one from spans.

### Fixture

```python
class Fixture:
    name: str
    path: Path
    context: str | None = None
```

A case's fixture file, downloaded before the task is called.

**Attributes**

- `name` (`str`): The fixture key the case names.
- `path` (`Path`): Where the file sits on this machine.
- `context` (`str | None`): The file's description from the fixture registry, or `None`.

### Judge

```python
class Judge:
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str
    reasoning_effort: str = 'high'
    max_tokens: int = 32768
```

The LLM judge an experiment uses; exported from `evalkit.sdk` as `Judge`.

Set exactly one of `provider` or `base_url`. With `provider`, the judge is served
by an LLM provider configured on the evalkit server: judge-using built-in
evaluators run on the server with its stored key. With `base_url`, you bring your
own judge: those evaluators run on your machine, with `api_key`.

**Attributes**

- `provider` (`str | None`): The name of an LLM provider configured on the server.
- `base_url` (`str | None`): An OpenAI-compatible endpoint that supports structured output.
- `api_key` (`str | None`): The key for `base_url`. Sent only to `base_url`; never stored on the evalkit server or shown.
- `model` (`str`): The model name at that endpoint.
- `reasoning_effort` (`str`): Passed to the model as `reasoning_effort`.
- `max_tokens` (`int`): The most tokens one judge reply may use.

### ExperimentResult

```python
class ExperimentResult:
    experiment_id: int | None
    url: str | None
    runs: list[RunSummary]
```

The result of `run_experiment` or `evaluate_experiment`.

**Attributes**

- `experiment_id` (`int | None`): The experiment, or `None` after a dry run.
- `url` (`str | None`): The experiment's page in the web UI, or `None` after a dry run.
- `runs` (`list[RunSummary]`): One entry per case and repetition.

### RunSummary

```python
class RunSummary:
    case_id: str
    repetition: int
    status: str
    trajectory_status: str | None
    session_id: str | None
    trajectory_note: str | None = None
    url: str | None = None
```

One run of an experiment.

**Attributes**

- `case_id` (`str`): The case.
- `repetition` (`int`): 1 for the first repetition, 2 for the second, and so on.
- `status` (`str`): `completed`, `failed`, `cancelled` or `timeout`.
- `trajectory_status` (`str | None`): `valid`, `partial`, `invalid` or `unavailable`, or `None` when no trajectory was read.
- `session_id` (`str | None`): The session the run's spans belong to.
- `trajectory_note` (`str | None`): Why the trajectory is partial or unavailable, or a note on a valid one whose mapping read no LLM or tool steps; `None` otherwise.
- `url` (`str | None`): The run's page in the web UI: the experiment's page for its case, or after a dry run the agent run's page; `None` when a dry run pushed its trajectory or has no session id, because the server then holds no run.
