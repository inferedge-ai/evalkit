# 07. Local harness run

A developer runs their own agent, served by a harness on their machine, against a dataset. The CLI drives the harness and scores the runs; results land on the evalkit server.

## Actors

- **Developer**: runs `evalkit run`, `evalkit status`, `evalkit evaluate`.
- **CLI**: the `evalkit` command on the developer's machine. It reads `evalkit.yaml` (`cases` / `fixtures` dirs and the `credentials` map; a `.env` beside it is loaded).
- **Harness**: the developer's agent behind the Harness API. The agent lives in the developer's personal workspace (`evalkit agents create research-agent --harness-url http://localhost:8000`); its `harness_url` is often `localhost`.
- **Custom evaluator**: optional, a remote evaluator in the agent's workspace (`evalkit evaluators create`).
- **Judge**: the LLM used by built-in evaluators. The experiment uses the server's default judge unless the run sets one with `--judge-*` flags (`evalkit judge-default show` prints the default).
  - A judge served by an LLM provider configured on the server (the default, or `--judge-provider`) is called by the server: the CLI asks the server to score each judge-using evaluator.
  - A judge you bring (`--judge-base-url` with `--judge-api-key-env NAME`) is called by the CLI, with the key from that env var.
- **evalkit server**: stores the dataset, experiment, runs, scores and trajectories.

## Sequence

### Start and drive

```mermaid
sequenceDiagram
  actor Dev as Developer
  participant CLI
  participant S as evalkit server
  participant H as Harness

  Dev->>CLI: evalkit run finance --agent research-agent [--runs N] [--case-id ...] [--model M] [--judge-model J]
  CLI->>S: GET agent research-agent@#lt;me#gt;
  CLI->>CLI: refuse a code-driven agent (no harness_url) or another user's agent
  CLI->>CLI: find evalkit.yaml, resolve the agent's key from credentials
  opt dataset is a local dir under cases/ (and no --resume)
    CLI->>S: upload missing fixtures, sync cases into finance@#lt;me#gt;
    S-->>CLI: created / patched / deleted counts
  end
  CLI->>H: GET capabilities
  CLI->>S: POST experiment (dataset, agent, selection, repetitions, capabilities, judge, git sha/branch, lease holder)
  S->>S: check every case input against the agent's input_schema
  S->>S: pin case versions
  S-->>CLI: experiment id
  CLI-->>Dev: "experiment #lt;id#gt; (##lt;n#gt; of #lt;dataset#gt;)"
  loop each case × repetition (bounded by max_concurrent_runs)
    CLI->>S: renew lease
    CLI->>S: get case input + fixtures
    CLI->>H: start run, poll until terminal
    CLI->>H: GET trajectory (ATIF), or read the OTel session from S if the agent uses traces
    CLI->>S: store trajectory, record run (answer, status, metrics)
    CLI-->>Dev: "run #lt;case#gt; ##lt;rep#gt; #lt;status#gt;"
  end
  CLI->>S: status → evaluating
```

### Score and summarise

```mermaid
sequenceDiagram
  actor Dev as Developer
  participant CLI
  participant S as evalkit server
  participant J as Judge
  participant E as Custom evaluator

  CLI->>S: fetch experiment runs + pinned cases
  loop each run with an answer
    alt judge served by a server-side LLM provider (default)
      CLI->>S: POST experiment-runs/#lt;id#gt;/server-evaluations/#lt;name#gt; per judge-using built-in
      S->>J: judge with the provider's stored key
      S-->>CLI: score rows + judge token usage (nothing written)
    else own judge (--judge-base-url, --judge-api-key-env)
      CLI->>J: judge-using built-ins with the key from --judge-api-key-env
    end
    CLI->>CLI: tool_trajectory_match, required_tools_called, max_response_time, gauges
    CLI->>E: remote evaluators named in the case
    CLI->>S: publish scores and gauges
    CLI-->>Dev: "scored #lt;case#gt; ##lt;rep#gt; (#lt;n#gt; rows)"
  end
  CLI->>S: status → completed
  CLI->>S: GET scorecard
  CLI-->>Dev: buckets, evaluators, failing cases (overall or any evaluator < 0.80, naming the evaluators), errored cases, gauges
```

### Interrupt, resume, re-score, status

```mermaid
sequenceDiagram
  actor Dev as Developer
  participant CLI
  participant S as evalkit server

  Dev->>CLI: Ctrl-C during run
  CLI->>S: status → failed ("interrupted: cancelled by #lt;user#gt;")
  CLI-->>Dev: "interrupted" (exit 130)

  Dev->>CLI: evalkit run finance --agent research-agent --resume #lt;id#gt;
  Note over CLI: no sync, no new experiment
  CLI->>S: status failed → running (clears the error, takes the lease)
  CLI->>S: fetch runs, skip (case, rep) already completed
  CLI->>CLI: drive the rest, then score everything not yet scored

  Dev->>CLI: evalkit evaluate #lt;id#gt; [--refetch-trajectories]
  CLI->>S: GET experiment, refuse if it used Python evaluators
  CLI->>S: status → evaluating (takes the lease)
  opt --refetch-trajectories
    CLI->>S: poll OTel sessions again for runs whose trajectory was partial/unavailable
    CLI->>S: overwrite stored trajectory, force re-score those runs
  end
  CLI->>S: score runs with the experiment's recorded judge, keep successful scores, retry errored ones
  CLI-->>Dev: "evaluated #lt;id#gt;"

  Dev->>CLI: evalkit status #lt;id#gt;
  CLI->>S: GET experiment
  CLI-->>Dev: id, #number of dataset, agent, status, ran/evaluated counts, lease holder, error
```

## Expected outcomes

- `evalkit run <dir-or-name> --agent research-agent` syncs the local cases into `<dataset>@<me>` first, then runs; the experiment appears under that dataset in the UI.
- Progress lines: `experiment <id> (#<n> of <dataset>)`, one `run <case> #<rep> <status>` per run, one `scored …` per run, then the summary table.
- Every selected case runs, whatever its review state, in personal and shared datasets (e.g. `finance@dev`) alike.
- `--case-id` limits the selection; `--runs N` gives N repetitions per case.
- `--model` is recorded on the experiment; if the harness doesn't accept a model override the server logs a warning and still runs.
- If the harness accepts neither files nor file URLs, fixture cases are dropped with `skipped <n> case(s) with fixture files; …` and the expected-run count shrinks to match.
- A personal agent's experiment lives in the personal workspace of the user who starts it, even against a shared dataset such as `finance@prod`.
- The experiment's status moves `running` → `evaluating` → `completed`, or `failed`.
- The CLI holds a lease on the experiment while it works and renews it. If the CLI dies, the server marks the experiment `failed` once the lease expires; it can then be resumed.
- Ctrl-C leaves the experiment `failed` with error `interrupted: cancelled by <user>`; `--resume <id>` finishes it without repeating completed runs.
- On resume, the latest attempt per (case, repetition) replaces the failed one in the scorecard.
- The experiment pinned its case versions at start. Syncing again later does not change it, and `--resume` doesn't sync.
- `evalkit evaluate <id>` is safe to repeat: successful scores stay, errored ones are retried.
- `evalkit status <id>` prints the status, `<ran>/<total> ran, <evaluated>/<total> evaluated`, the lease holder while it runs, and the error line when set.
- The UI shows no server-side Resume or Re-evaluate for local runs. It shows the `evalkit run … --resume <id>` command instead.
- Local runs appear in the agent's Runs browser (`/runs?agent=research-agent@<me>`).
- Each score records where it ran: `server` for judge-using built-ins whose judge is served by an LLM provider configured on the server, `local` for everything else.

## Error cases

- Another user's agent: `<agent> belongs to <ws>'s workspace; only its owner can run it`.
- No `evalkit.yaml` above the current dir: `no evalkit.yaml found above the current directory`.
- Code-driven agent (no `harness_url`): `<agent> is a code-driven agent (no harness_url); run it with evalkit.sdk.run_experiment, or if it serves the Harness API, set its URL: evalkit agents edit <agent> --harness-url URL`.
- Unknown agent: `unknown agent: <ref> — create it with evalkit agents create`.
- Nothing left to run (no case matches, or every case needs fixtures the harness can't take): `nothing to run in <dataset>: …`.
- Case inputs that fail the agent's `input_schema`: `<n> case input(s) fail agent <agent>'s input_schema:` listing every bad case; nothing runs.
- `--judge-base-url` without `--judge-api-key-env`: `--judge-base-url needs --judge-api-key-env NAME, the key's env var`, before the run. `--judge-api-key-env` naming an unset variable: `env var NAME is not set …`. Re-scoring (`evalkit evaluate <id>`) or resuming (`--resume <id>`) such an experiment without the flag: `your judge at <url> needs its API key: pass --judge-api-key-env NAME …`, before anything runs; the experiment's status does not change.
- `--judge-provider` with `--judge-base-url`: `pass --judge-provider or --judge-base-url, not both`.
- A server-evaluator call that fails or times out (900 s, `EVALKIT_SERVER_EVALUATOR_TIMEOUT`): that evaluator gets an `error` score for the run, scoring goes on, and `evalkit evaluate <id>` retries it. The call is never retried on its own.
- A `credentials` entry whose env var is unset: `credentials.<kind>s['<ref>'] needs env var <NAME>`, before the run.
- No `credentials.agents` entry for the agent: the CLI prints "no credentials.agents entry for X in evalkit.yaml; calling the harness without x-api-key" and sends no key header. A harness that needs one returns 401, which shows as a run error. A missing `credentials.evaluators` entry prints the same kind of line.
- CLI / server version mismatch: refused before any work.
- A second `--resume` or `evaluate` on an experiment whose lease is live: 409.
- Harness run timeout: the run is recorded with status `timeout` and error `run timed out after <n>s`.
- Harness protocol violation: run recorded with `harness protocol violation: …`.
- Trajectory invalid or unreachable: run recorded with `trajectory_status` `invalid` / `unavailable`; scoring continues without it.
- Scoring fails for some runs: `scoring failed for X of Y runs of experiment <id>; first error: …` plus `finish it with: evalkit evaluate <id>`; the experiment is `failed`.
- `evalkit evaluate` on an experiment that used Python evaluators: `experiment <id> used python evaluators <names>; re-score it with evalkit.sdk.evaluate_experiment from the script that defines them`.
- Server-side resume or re-evaluate of a local run: 409 pointing to `evalkit run <dataset> --agent <agent> --resume <id>`.
