# Beyond the quickstart

What the [Quickstart](quickstart.md) leaves out: other ways to connect your agent, cases you write yourself, files attached to a case, and sharing cases with your team.

## Other ways to connect your agent

| Path                 | What you build                                                                                    | Pick it when                                                                                                     |
| -------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| SDK run + traces     | A Python script that calls your agent once per case; evalkit reads the run's traces               | The default, and the one the quickstart uses. Runs start from your machine or CI                                 |
| Harness API          | An HTTP service: start a run, report its status, serve its ATIF trajectory                        | A deployed agent the server should drive (shared agents, runs started from the UI), or you need the Workspace tab |
| Harness API + traces | The same service minus the trajectory endpoint; the trajectory comes from your spans              | You want the Harness API but your agent already emits OpenTelemetry spans                                        |

The Workspace tab on a run lists the files the agent wrote. evalkit reads them through the Harness API files endpoints, so only the two Harness API paths have it. See [Harness API](harness-api.md) for what each endpoint enables.

Create a Harness API agent with its URL:

```bash
evalkit agents create myagent --harness-url http://localhost:8080/evalkit/v1
```

Check it, then run a dataset against it:

```bash
evalkit harness check myagent
```

```bash
evalkit run smoke --agent myagent
```

`evalkit harness check` exercises every capability the harness declares. A run executes every case it selects, whether the dataset is in your own workspace (`smoke`) or a shared one (`smoke@dev`). Review state never filters a run. If the agent needs an API key, name it in `evalkit.yaml`; see [Config](config.md).

## Write a case by hand

Each directory under `evalkit/cases` is a dataset. Add `evalkit/cases/smoke/cases.yaml`:

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

The next run of that dataset syncs the folder first. [Case schema](case-schema.md) lists every key and evaluator.

## Attach a file to a case

Drop the file in `evalkit/fixtures`, register it once in `fixtures.yaml` at the root of your cases directory, and name it in the case. The `context` line is what the agent is told about the file.

```bash
cp ~/Downloads/meridian-balance-sheet.csv evalkit/fixtures/
```

```bash
shasum -a 256 evalkit/fixtures/meridian-balance-sheet.csv
```

```yaml
# evalkit/cases/fixtures.yaml
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

## Read and compare experiments

On the server, open the dataset under Datasets and pick the experiment. The experiment page has the scorecard; each run opens its trajectory and scores and, on the Harness API paths, its workspace files. If converted spans look wrong (missing messages, wrong span kinds), check one session with `evalkit traces check myagent <session id>` and see [Mappings](mappings.md).

List a dataset's experiments, newest first:

```bash
evalkit experiments list --dataset smoke
```

Compare two experiments: cases fixed, cases broken and gauge deltas:

```bash
evalkit summary <new-id> --baseline <old-id>
```

Re-score the same runs after an evaluator change, with no agent calls:

```bash
evalkit evaluate <experiment-id>
```

Download every run (trajectory, case, scores) to diagnose failures:

```bash
evalkit runs pull --experiment <experiment-id>
```

## Share cases with your team

Cases reach a shared dataset through a PR. On merge, CI syncs them into a shared workspace such as `dev`, where they start Unreviewed; a reviewer of that workspace approves them, and if `dev` is linked to `prod`, approval promotes them there. [CI sync](ci-sync.md) shows how to set up that CI job, and [Where runs happen](workspaces.md) walks through the whole setup.

```bash
evalkit review request finance@dev --case-id net-debt-fy2025 --reviewer <user>
```

```bash
evalkit review approve finance@dev net-debt-fy2025
```

Only reviewers can approve. If an admin linked `dev` to a promotion target (`evalkit workspaces promote-to dev prod`), that approval also lands the case in `finance@prod`, approved. `prod` takes cases only that way: no sync, no separate review. The case page in either workspace shows the other's version and links to it.
