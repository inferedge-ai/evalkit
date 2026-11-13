# Quickstart

Connect your agent, turn its real traces into eval cases, and ship a measured improvement. It takes about 15 minutes, and your coding agent does most of it.

You need:

- An agent that already sends traces to Arize Phoenix. No traces yet? See [No trace history?](#no-trace-history)
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).
- A coding agent, such as Claude Code or Codex.

## Let your coding agent drive

In your agent's project folder, install evalkit:

```bash
uv tool install evalkit-py
```

Then tell your coding agent:

```text
Run `evalkit skills install`, then use the getting-started skill to set up evalkit for this agent.
```

It runs every step below. It stops only when it needs you: the sign-in in your browser, and your trace store's URL and API key.

Prefer to do it by hand? The steps are the same.

## 1. Log in

```bash
evalkit login
```

Your browser opens. Sign up or sign in to [hosted evalkit](concepts.md#hosted-evalkit). There is no server to run and no LLM key to add: the [judge](concepts.md#judge) is ready.

On a server you run yourself:

```bash
evalkit login --url {{origin}}
```

## 2. Connect your agent

Tell evalkit where your traces live. This makes a [trace source](concepts.md#trace-source), and it needs an admin.

<!-- tabs -->
#### CLI

```bash
evalkit trace-sources create phoenix --url <your Phoenix URL> --api-key-env PHOENIX_API_KEY
```

#### UI

Open Settings › Organization › Trace sources and add one named `phoenix`.
<!-- /tabs -->

Create your [agent](concepts.md#agent) with the [preset](concepts.md#preset) for your framework, and name its project in your trace store. A preset is a ready-made [mapping](concepts.md#mapping): it tells evalkit how to read your spans. List the presets:

```bash
evalkit traces mapping presets
```

<!-- tabs -->
#### CLI

```bash
evalkit agents create myagent --trajectory-source otel --mapping langchain --trace-source phoenix --project <project>
```

#### UI

Open Agents › New agent, set Trajectories to "From traces (OTel spans)", pick the preset, and pick the trace source and its project.
<!-- /tabs -->

Check that evalkit reads your traces. Import one [session](concepts.md#trace-span-session) from your Phoenix project:

```bash
evalkit traces import myagent <session id>
```

Every line should have a ✓. If one has a `!`, your spans differ from the preset: the connect-traces skill adjusts the mapping. See [Framework mappings](framework-mappings.md) and [Mappings](mappings.md).

## 3. Mine cases from real runs

Bring in your recent history:

```bash
evalkit traces import myagent --since <date>
```

Each imported session is an [app run](concepts.md#app-run). Tell your coding agent:

```text
Use the eval-driven-development skill to mine cases from myagent's 30 most recent app runs into a dataset named mined.
```

It reads each run, groups the ones that failed or ran slow, shows you a plan, and writes the [cases](concepts.md#case) to `evalkit/cases/mined/`. That folder is your first [dataset](concepts.md#dataset), built from real questions. [Case schema](case-schema.md) has the format.

## 4. Run the baseline

Add evalkit to your project:

```bash
uv add evalkit-py
```

Write `evalkit/run.py`. The [task](concepts.md#task) calls your agent once per case:

```python
from evalkit.sdk import run_experiment
from myagent import ask


def task(input: dict) -> str:
    return ask(input["question"])


if __name__ == "__main__":
    run_experiment(dataset="mined", agent="myagent", task=task)
```

Run it from your project's root:

```bash
uv run python evalkit/run.py
```

Your agent runs as usual and keeps sending traces to Phoenix. evalkit picks up each run's [trajectory](concepts.md#trajectory) from there, scores it, and prints the [scorecard](concepts.md#scorecard) and a link to the [experiment](concepts.md#experiment).

Some cases fail. Good: they came from runs that went wrong. Does your agent run in another process, or start its own trace? See [SDK runs](sdk-runs.md#an-agent-with-its-own-trace-store).

## 5. Improve

Tell your coding agent:

```text
Use the eval-driven-development skill to fix the failing cases in the mined dataset, then run it again.
```

It pulls the failed [runs](concepts.md#run), finds the cause of each, fixes your agent and runs the dataset again. Then compare the two experiments:

```bash
evalkit experiments list --dataset mined
```

```bash
evalkit summary <new id> --baseline <old id>
```

You see which cases it fixed, and how response time, cost and tokens moved.

## What you just did

- Set up evalkit with no server and no LLM key.
- Mapped your agent's spans.
- Built a dataset from your own traces.
- Ran an experiment and got a scorecard.
- Found what went wrong, fixed it, and measured the gain.

Or, in the words of DHH:

![Look at all the things I'm not doing](images/look-at-all-the-things-im-not-doing.png)

## No trace history?

Send your agent's traces straight to evalkit instead. Create the agent as in step 2, without `--trace-source` and `--project`, then make it a token:

```bash
evalkit tokens create --agent myagent
```

Export the token, or add it to your project's `.env`:

```bash
export EVALKIT_OTEL_TOKEN=<token>
```

Add one line to `evalkit/run.py`, before your agent runs:

```python
from evalkit.sdk import tracing

tracing.register(agent="myagent")
```

Use your agent a few times, then carry on from step 3 without the import. [Tracing](tracing.md) has the details.

## Where next

- [Beyond the quickstart](beyond-quickstart.md): write a case by hand, attach files, drive your agent through the Harness API, share cases with your team.
- [Concepts](concepts.md): every term on this page.
- [Try locally](try-locally.md): run evalkit on your own machine with a sample agent.
