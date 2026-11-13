# Quickstart

Connect your agent, turn its real runs into eval cases, and ship a measured improvement. Your coding agent does most of the work. Plan for about half an hour; mining cases takes the longest.

You need:

- An agent. The steps show Python; step 4 links what to do for other languages.
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).
- A coding agent, such as Claude Code or Codex.

There are two paths. They differ only in step 4, which has a tab for each:

- **My agent sends traces to Phoenix.** evalkit imports your trace history.
- **No traces yet.** Your agent sends its traces straight to evalkit, and you use it a few times.

## Let your coding agent drive

Open your coding agent in your agent's project folder and tell it:

```text
Install evalkit with `uv tool install evalkit-py`, run `evalkit skills install`, then use the getting-started skill to set up evalkit for this agent.
```

It runs every step below. It stops only when it needs you: the sign-in in your browser, which path you take, and your trace store's URL and API key.

Prefer to do it by hand? The steps are the same.

## 1. Install and log in

From your agent's project folder:

```bash
uv tool install evalkit-py
```

```bash
evalkit login
```

Your browser opens. Sign up or sign in to [hosted evalkit](concepts.md#hosted-evalkit). There is no server to run and no LLM key to add: the [judge](concepts.md#judge) is ready.

On a server you run yourself:

```bash
evalkit login --url {{origin}}
```

## 2. Add evalkit to your project

From your project's root:

```bash
uv add evalkit-py
```

This needs `requires-python` in your `pyproject.toml` to start at 3.12 or newer (`">=3.12"`). Is your repo not a Python project, or can it not move to 3.12? Give evalkit its own uv project in the `evalkit/` folder instead: see [The CLI, inside a project](install.md#the-cli-inside-a-project).

## 3. Create your agent

Pick the [preset](concepts.md#preset) for your framework. A preset is a ready-made [mapping](concepts.md#mapping): it tells evalkit how to read your agent's spans. [Framework mappings](framework-mappings.md) says which preset fits which framework. List them:

```bash
evalkit traces mapping presets
```

Create your [agent](concepts.md#agent) with it:

<!-- tabs -->
#### CLI

```bash
evalkit agents create myagent --trajectory-source otel --mapping langchain
```

#### UI

Open Agents › New agent, set Trajectories to "From traces (OTel spans)" and pick the preset.
<!-- /tabs -->

## 4. Get real runs onto evalkit

Each [session](concepts.md#trace-span-session) of your agent becomes an [app run](concepts.md#app-run). Pick your path:

<!-- tabs -->
#### My agent sends traces to Phoenix

Tell evalkit where your traces live. This makes a [trace source](concepts.md#trace-source), and it needs an admin. In the UI, it is Settings › Organization › Trace sources.

```bash
evalkit trace-sources create phoenix --url <your Phoenix URL> --api-key-env PHOENIX_API_KEY
```

Point your agent at its project there:

```bash
evalkit agents edit myagent --trace-source phoenix --project <project>
```

Check that evalkit reads your traces. Import one session from the project:

```bash
evalkit traces import myagent <session id>
```

Every line should have a ✓. A `!` means your spans differ from the preset: the connect-traces skill fixes the mapping. See [Mappings](mappings.md).

Bring in your recent history:

```bash
evalkit traces import myagent --since <date>
```

Does your agent run in another process, or start its own trace? Read [An agent with its own trace store](sdk-runs.md#an-agent-with-its-own-trace-store) before step 6.

#### No traces yet

Make your agent a token for sending traces:

```bash
evalkit tokens create --agent myagent
```

Export it in every shell that runs your agent:

```bash
export EVALKIT_OTEL_TOKEN=<token>
```

Add the OpenTelemetry instrumentor for your framework. The Instrumentation column of [Framework mappings](framework-mappings.md) names it. For LangGraph or LangChain:

```bash
uv add openinference-instrumentation-langchain
```

Turn tracing on where your agent starts, in code that also runs when `evalkit/run.py` imports your agent:

```python
from evalkit.sdk import tracing
from openinference.instrumentation.langchain import LangChainInstrumentor

tracing.register(agent="myagent")
LangChainInstrumentor().instrument()
```

`register` only adds evalkit as a destination; the instrumentor makes the spans.

Give each run its own session. Wrap each question your agent answers:

```python
from uuid import uuid4

from openinference.instrumentation import using_session

with using_session(str(uuid4())):
    answer = agent.ask(question)
```

Other instrumentors: see [a session id on each turn](framework-mappings.md#every-framework-a-session-id-on-each-turn).

Now use your agent the way you normally do: ask it 5 to 10 real questions.

Check one session. List the runs, then check one by its id:

```bash
evalkit runs list myagent --source app
```

```bash
evalkit traces check myagent <session id>
```

Every line should have a ✓. A `!` means your spans differ from the preset: the connect-traces skill fixes the mapping. See [Mappings](mappings.md).

Is your agent not in Python, or does it run in another process? Point its exporter at evalkit as in [From any OpenTelemetry SDK](tracing.md#from-any-opentelemetry-sdk), and link its sessions to runs as in [An agent in another process](sdk-runs.md#an-agent-in-another-process).
<!-- /tabs -->

[Tracing](tracing.md) has the details for both paths.

## 5. Mine cases from real runs

Tell your coding agent:

```text
Use the eval-driven-development skill to mine cases from myagent's app runs since <date> into a dataset named mined. Keep the first batch to 30 runs or fewer.
```

It reads each run, groups the ones that failed or ran slow, shows you a plan, and writes the [cases](concepts.md#case) to `evalkit/cases/mined/`. That folder is your first [dataset](concepts.md#dataset), built from real questions. [Case schema](case-schema.md) has the format.

## 6. Run the baseline

Write `evalkit/run.py`. The [task](concepts.md#task) calls your agent once per case:

```python
from evalkit.sdk import run_experiment

from myagent import ask  # however your code calls your agent


def task(input: dict) -> str:
    return ask(input["question"])


if __name__ == "__main__":
    result = run_experiment(dataset="mined", agent="myagent", task=task)
    print(result.url)
```

Is your agent async? Write `async def task` and `await` it. Do not wrap it in `asyncio.run`. See [The task](sdk-runs.md#the-task).

Run it from your project's root:

```bash
uv run python evalkit/run.py
```

evalkit picks up each run's [trajectory](concepts.md#trajectory), scores it, and prints the [scorecard](concepts.md#scorecard). The script then prints the link to the [experiment](concepts.md#experiment).

Some cases fail. Good: they came from runs that went wrong.

## 7. Improve

Tell your coding agent:

```text
Use the eval-driven-development skill with --apply to fix the failing cases of my latest experiment on the mined dataset, then run the whole dataset again.
```

It pulls the failed [runs](concepts.md#run), finds the cause of each, fixes your agent and runs the dataset again. Does your agent run as a service? Restart it after each fix, before the next run.

Then compare the two experiments:

```bash
evalkit experiments list --dataset mined
```

```bash
evalkit summary <new id> --baseline <old id>
```

You see which cases it fixed, and how response time, cost and tokens moved.

## What you just did

- Set up evalkit in your agent repo.
- Mapped your agent's spans.
- Built a dataset from your agent's real runs.
- Ran an experiment and got a scorecard.
- Found what went wrong, fixed it, and measured the gain.

## Where next

- [Beyond the quickstart](beyond-quickstart.md): write a case by hand, attach files, drive your agent through the Harness API, share cases with your team.
- [Concepts](concepts.md): every term on this page.
- [Try locally](try-locally.md): run evalkit on your own machine with a sample agent.
