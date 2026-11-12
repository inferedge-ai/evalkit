# Config: `evalkit.yaml`

`evalkit.yaml` sits at the root of the repository that holds your eval cases or your agent. The CLI and the SDK look for it above the current directory. It names the case and fixture directories and the API keys the runs from this machine use.

The file is optional. Without one, the CLI and the SDK act as if an `evalkit.yaml` sat in the current directory with `cases: evalkit/cases`, `fixtures: evalkit/fixtures` and no credentials. So with your cases in `evalkit/cases/<dataset>/`, `evalkit sync <dataset>`, `evalkit run <dataset>` and `run_experiment` work with no file. You need the file to keep cases or fixtures somewhere else, or to give an agent or evaluator an API key.

```yaml
cases: evalkit/cases # optional, this is the default: directory whose children are datasets
fixtures: evalkit/fixtures # optional, this is the default: fixture bytes, flat, keyed by filename

credentials: # values are env:NAME, read when a run needs them
  agents:
    myagent: env:MYAGENT_KEY # a bare name means your own workspace
  evaluators:
    citation_check: env:EVALUATOR_KEY
```

Only your personal Harness API agents use `credentials.agents` here. The server drives shared agents (`ouragent@dev`) with the keys stored in its database, so an entry for one in `evalkit.yaml` is never read. The same holds for shared remote evaluators.

Put the values in a `.env` file next to `evalkit.yaml` (and keep it out of git), or in your shell. A variable set in the shell wins over `.env`. Without an `evalkit.yaml`, the `.env` in the current directory is read.

Agents, remote evaluators and the judge are not configured here:

- **Agents** are created once on the server with `evalkit agents create` (or on the Agents page). A personal agent runs from your machine; a shared one is driven by the server. An agent without a harness URL is code-driven: only `run_experiment` can run it. See [SDK runs](sdk-runs.md).
- **Remote evaluators** are created with `evalkit evaluators create`. A run uses the evaluators in its agent's workspace.
- **The judge** of an experiment is the server's default judge (an admin sets it with `evalkit judge-default set`) unless you pick one. Re-evaluating an experiment always reuses its judge. You pick it in one of two ways:
  - **An LLM provider configured on the server:** `evalkit run … --judge-provider NAME --judge-model M`, or `run_experiment(judge=Judge(provider=..., model=...))`. The default judge is one too. The evaluators that use a judge (`answer_rubrics`, `presentation_judge`, `trajectory_rubrics`) then run on the evalkit server with its stored key, so you need no judge key on your machine.
  - **Your own judge:** `evalkit run … --judge-base-url URL --judge-model M --judge-api-key-env NAME`, where `NAME` is the env var (or `.env` entry) holding the key; or `run_experiment(judge=Judge(base_url=..., model=..., api_key=...))`. Those evaluators then run on your machine. The key goes only to your judge and is never stored on the evalkit server, so re-scoring and resuming ask for it again: `evalkit evaluate <id> --judge-api-key-env NAME`, `evalkit run … --resume <id> --judge-api-key-env NAME`, or `evaluate_experiment(..., judge=Judge(base_url=..., model=..., api_key=...))`, the same judge again. A different judge is refused.
  - `--judge-provider` and `--judge-base-url` together are refused, and so is `--judge-base-url` without `--judge-api-key-env`.

Rules:

- `env:NAME` values are read only when a command needs them. A missing variable is an error that names it.
- An agent or evaluator with no `credentials` entry is called without a key, and the run prints one line saying so.
- `credentials` holds agent and evaluator keys only. A file with `credentials.llm_providers` (or the older `credentials.judges`) fails to load: pass your own judge's key with the run instead.
- `cases` and `fixtures` are optional. When left out they are `evalkit/cases` and `evalkit/fixtures`, relative to the directory of `evalkit.yaml`, or to the current directory when there is no file. Neither directory has to exist; a command fails only when it needs something that is not there:
  - `evalkit sync <name>` and `evalkit run <name>` on a dataset in your workspace read `<cases>/<name>`. When that directory is missing, the error names the cases directory it looked in. Passing the dataset's directory instead (`evalkit sync datasets/finance`) works from anywhere.
  - A directory under the cases root always belongs to the dataset named after its first folder: `evalkit sync evalkit/cases/finance/statements` patches the `statements` folder of `finance`.
  - A sync uploads the fixture bytes the server does not hold yet from `fixtures`. When a registered fixture is neither on the server nor in that directory, the sync fails and names the file it looked for.
  - `run_experiment` on a dataset in your workspace syncs `<cases>/<name>` first. When that directory is missing it says so, skips the sync and runs the copy the server holds.
- Keep `__init__.py` out of a folder named `evalkit`. With that file the folder is a Python package that hides the installed `evalkit` package from `import evalkit`, so the CLI and the SDK refuse it as the place for cases or fixtures.
