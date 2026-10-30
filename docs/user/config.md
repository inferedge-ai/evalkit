# Config: `evalkit.yaml`

`evalkit.yaml` sits at the root of the repository that holds your eval cases or your agent. The CLI and the SDK look for it above the current directory. It names the case and fixture directories and the API keys the runs from this machine use.

```yaml
cases: eval/cases # optional: directory whose children are datasets
fixtures: eval/fixtures # optional: fixture bytes, flat, keyed by filename

credentials: # values are env:NAME, read when a run needs them
  agents:
    myagent: env:MYAGENT_KEY # a bare name means your own workspace
  evaluators:
    citation_check: env:EVALUATOR_KEY
  llm_providers:
    https://litellm.example.com: env:LITELLM_API_KEY # keyed by the judge's base URL
```

Only your personal Harness API agents use `credentials.agents` here. The server drives shared agents (`ouragent@dev`) with the keys stored in its database, so an entry for one in `evalkit.yaml` is never read. The same holds for shared remote evaluators.

Put the values in a `.env` file next to `evalkit.yaml` (and keep it out of git), or in your shell. A variable set in the shell wins over `.env`.

Agents, remote evaluators and the judge are not configured here:

- **Agents** are created once on the server with `evalkit agents create` (or on the Agents page). A personal agent runs from your machine; a shared one is driven by the server. An agent without a harness URL is code-driven: only `run_experiment` can run it. See [SDK runs](sdk-runs.md).
- **Remote evaluators** are created with `evalkit evaluators create`. A run uses the evaluators in its agent's workspace.
- **The judge** of an experiment is the server's default judge (an admin sets it with `evalkit judge-default set`) unless you pick one: `evalkit run … --judge-model M --judge-base-url URL --judge-effort E`, the run dialog's "Judge provider" picker, or `run_experiment(judge=Judge(...))`. Re-evaluating an experiment always reuses its judge.

Rules:

- `env:NAME` values are read only when a command needs them. A missing variable is an error that names it.
- An agent or evaluator with no `credentials` entry is called without a key, and the run prints one line saying so. A judge with no entry stops the run before its first case: `no credentials.llm_providers entry for <url>`.
- `credentials.judges` is now `credentials.llm_providers`. A file that still uses the old name fails to load.
- An `llm_providers` key may itself be `env:NAME` (`env:LLM_BASE_URL: env:LLM_API_KEY`), so the judge's base URL can come from the environment too. A key whose variable is unset is skipped.
- `cases` and `fixtures` are optional. Leave them out when you only run shared datasets, for example from `run_experiment`. Commands that need them say so:
  - `evalkit sync <name>` and `evalkit run <name>` on a dataset in your workspace need `cases` to find `<cases>/<name>`. Passing the dataset's directory instead (`evalkit sync eval/cases/finance`) works without it.
  - A sync uploads fixture bytes from `fixtures`. Without it, a case whose fixture the server does not already hold fails the sync with `E-FIXTURE-MISSING`.
  - `run_experiment` on a dataset in your workspace skips the sync without `cases` and runs the copy the server holds.
  - Fixtures a run downloads go to `fixtures`, or to a temporary directory when it is not set.
