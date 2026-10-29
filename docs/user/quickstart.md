# Quickstart

From nothing to a scored experiment on an evalkit server your team already runs. Each step links to its full page; [Concepts](concepts.md) explains the terms.

No server yet? [Try locally](try-locally.md) runs one on your machine with a sample agent and your own LLM, and ends with how to deploy one for your team.

## 1. Pick how evalkit reaches your agent

| Path                 | What you build                                                                                    | Pick it when                                                                                                     |
| -------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| SDK run + traces     | A Python script that calls your agent once per case, and an OpenTelemetry exporter to this server | The default: start here. Runs start from your machine or CI                                                     |
| Harness API          | An HTTP service: start a run, report its status, serve its ATIF trajectory                        | A deployed agent the server should drive (shared agents, runs started from the UI), or you need the Workspace tab |
| Harness API + traces | The same service minus the trajectory endpoint; the trajectory comes from your spans              | You want the Harness API but your agent already emits OpenTelemetry spans                                        |

The Workspace tab on a run lists the files the agent wrote. evalkit reads them through the Harness API files endpoints, so only the two Harness API paths have it. See [Harness API](harness-api.md) for what each endpoint enables.

## 2. Install and log in

```bash
uv tool install --reinstall evalkit --index {{origin}}/api/cli/simple
evalkit login --url {{origin}}
```

More in [Install](install.md) and [Login](login.md).

## 3. Write `evalkit.yaml` and one case

At the root of the repository that holds your eval cases:

```yaml
cases: eval/cases
fixtures: eval/fixtures

credentials:
  agents:
    myagent: env:MYAGENT_API_KEY # only for a Harness API agent that needs a key
  llm_providers:
    https://litellm.example.com: env:LITELLM_API_KEY # the server's judge base URL
```

Put the keys in a `.env` next to `evalkit.yaml`. Then create your agent on the server once:

```bash
# SDK path: a code-driven agent whose trajectory comes from its traces
evalkit agents create myagent --trajectory-source otel --mapping openinference.json
# Harness API path
evalkit agents create myagent --harness-url http://localhost:8080/evalkit/v1
```

`openinference.json` (or `gen_ai.json`) comes from the `connect-traces` skill.

Each directory under `cases` is a dataset. Add `eval/cases/smoke/cases.yaml`:

```yaml
cases:
  - id: store-count
    input:
      question: "How many company-operated stores did Starbucks have at FY2021 year-end?"
    evaluators:
      output:
        answer_rubrics:
          rubrics:
            - rubric: "States 17,133 company-operated stores at FY2021 year-end."
              type: factual_retrieval
```

More in [Config](config.md).

## 4. Run it

SDK path: install the SDK into your project (see [Install](install.md)), then write `eval/run.py`:

```python
from evalkit.sdk import run_experiment, tracing
from myagent import ask

tracing.register(agent="myagent")

def task(input: dict) -> str:
    return ask(input["question"])

if __name__ == "__main__":
    run_experiment(dataset="smoke", agent="myagent", task=task, dry_run=True)
```

`dry_run=True` runs one case without creating an experiment and prints what the server made of the spans. When it looks right, drop `dry_run`; `run_experiment` then returns a result whose `url` is the experiment page. More in [SDK runs](sdk-runs.md) and [Tracing](tracing.md).

Harness API path:

```bash
evalkit harness check myagent    # exercises every capability the harness declares
evalkit run smoke --agent myagent
```

A run executes every case it selects, whether the dataset is in your own workspace (`smoke`) or a shared one (`smoke@dev`). Review state never filters a run.

## 5. Read the results

`evalkit run` prints the scorecard when it finishes. On this server, open the dataset under Datasets and pick the experiment. The experiment page has the scorecard; each run opens its trajectory, scores and, on the Harness API paths, its workspace files. If converted spans look wrong (missing messages, wrong span kinds), check one session with `evalkit traces check myagent <session id>` and see [Mappings](mappings.md).

## 6. Attach a file to a case

Drop the file in the `fixtures` directory from your `evalkit.yaml`, register it once in `fixtures.yaml` at the root of your `cases` directory, and name it in the case. The `context` line is what the agent is told about the file.

```bash
cp ~/Downloads/meridian-balance-sheet.csv <fixtures>/
shasum -a 256 <fixtures>/meridian-balance-sheet.csv       # sha256 for the registry
```

```yaml
# <cases>/fixtures.yaml
fixtures:
  - key: meridian-balance-sheet.csv
    sha256: 652d32…eece7
    context: Meridian's FY2025 balance sheet, USD thousands.
```

```yaml
# in the case
- id: net-debt-fy2025
  fixtures: [meridian-balance-sheet.csv]
  input:
    question: Using only the attached file, what was net debt at FY2025 year-end?
```

A run or sync uploads any fixture whose sha256 the server doesn't hold yet, and the server keeps one copy per sha256. So you only need the files you touch, and if the bytes on disk don't match the registry the run stops and shows both hashes.

## 7. Iterate and promote

```bash
evalkit evaluate <experiment-id>        # re-score the same runs after an evaluator change; no agent calls
evalkit runs pull --experiment <experiment-id>   # download every run (trajectory, case, scores) to diagnose failures
```

Cases reach a shared dataset through a PR. On merge, CI syncs them into a shared workspace such as `dev`, where they start Unreviewed; a reviewer of that workspace approves them, and if `dev` is linked to `prod`, approval promotes them there.

```bash
evalkit review request finance@dev --case-id net-debt-fy2025 --reviewer <user>
evalkit review approve finance@dev net-debt-fy2025   # reviewers only
```

If an admin linked `dev` to a promotion target (`evalkit workspaces promote-to dev prod`), that approval also lands the case in `finance@prod`, approved. `prod` takes cases only that way: no sync, no separate review. The case page in either workspace shows the other's version and links to it.
