# Install

evalkit is one Python package on PyPI, `evalkit-py`. It holds the `evalkit` command (the CLI), the `evalkit.sdk` module for SDK runs and, with the `server` extra, the server itself. It needs **Python 3.12 or newer**, for the CLI, the SDK and the server alike.

Your CLI and SDK must run the **same version as the evalkit server** you use: local runs and SDK runs refuse to start otherwise. The commands on this page pin version **{{version}}**. On your server's own `/docs/install` page, that is the server's version; `curl <server url>/api/cli` prints it anywhere.

## The CLI, as a global tool

```bash
uv tool install --reinstall "evalkit-py=={{version}}"
```

With pipx instead: `pipx install --force "evalkit-py=={{version}}"`.

`curl {{origin}}/api/cli` prints the server's version, for scripts and CI:

```bash
uv tool install --reinstall "evalkit-py==$(curl -fsSL {{origin}}/api/cli | jq -r .version)"
```

## The CLI, inside a project

To keep evalkit out of your global tools, add it to a uv project and run it with `uv run`. The CLI needs no extra; add `otel` if the same project uses `tracing.register` or SDK runs, and `server` if you also run the server from there:

```bash
uv add "evalkit-py=={{version}}"
uv run evalkit --help
```

If you give evalkit its own uv project in a folder of your agent's uv project (say `eval/`), create it with `uv init --no-workspace`:

```bash
cd eval && uv init --no-workspace && uv add "evalkit-py=={{version}}"
```

Without the flag, uv silently adds `eval/` to your agent project's workspace, so it shares your agent's lockfile.

## The SDK, into a project

`run_experiment` and `tracing.register` live in the same package as the CLI. The `otel` extra brings the OpenTelemetry SDK and the OTLP exporter. Pin the server's version; `uv.lock` records it:

```bash
uv add "evalkit-py[otel]=={{version}}"
```

With pip instead: `pip install "evalkit-py[otel]=={{version}}"`.

To keep evalkit out of the app's dependencies, run the eval script with it instead:

```bash
uv run --with "evalkit-py[otel]=={{version}}" python eval/run.py
```

## The server

`uv tool install --reinstall "evalkit-py[server]=={{version}}"` (or `pip install "evalkit-py[server]=={{version}}"`) installs the server too: the API, the web UI and migrations. To run one on your machine against a Postgres you already have:

```bash
evalkit init-server   # creates the database, writes config.yaml and .env in ./evalkit-try
cd evalkit-try && evalkit serve
```

[Try locally](try-locally.md) walks through it with a sample agent; [Server config](server-config.md) lists every `config.yaml` key; the evalkit-deployment skill covers running a server for a team.

## The skills

The skills teach a coding agent to write cases, connect traces and run evals. Copy them into your repo, from the repo root:

```bash
evalkit skills install
```

They go to `.claude/skills/` when `.claude/` exists, else `.agents/skills/`; `--target DIR` picks another directory. The user docs they link to go to `evalkit-docs/` beside them. The `connect-traces` skill's starting span mappings land in `connect-traces/references/` (`openinference.json`, `gen_ai.json`).

`skills install` also writes outside the repo: it appends the target directory to `~/.evalkit/skills-targets`, so that `evalkit update` can refresh it later.

## Updating

Every API response carries `X-Evalkit-Version`. The CLI warns when it differs from the server and refuses when the major versions differ. Local runs and SDK runs refuse any difference, so after a server deploy, from the project's root:

```bash
evalkit update
```

It reinstalls the CLI at the server's version and, when `pyproject.toml` pins `evalkit-py==…`, moves the pin and runs `uv sync`. It then refreshes the skills in every directory `evalkit skills install` wrote to on this machine (listed in `~/.evalkit/skills-targets`), and in `./.claude/skills` or `./.agents/skills` if they hold an earlier install; commit the changes in repos that track the skills. Pass `--no-skills` to skip that.

A CLI older than 0.3.0 cannot update itself: `evalkit update` fails with `KeyError: 'filename'`. Run the [global tool install](#the-cli-as-a-global-tool) command once instead; `evalkit update` works from then on.

A project that vendored an evalkit wheel before evalkit was on PyPI moves to PyPI once: `uv remove evalkit && uv add "evalkit-py[otel]=={{version}}"`.

Next: [Login](login.md). `evalkit login` logs in to hosted evalkit, `https://evalkit.inferedge.com`; for any other server, pass its URL: `evalkit login --url {{origin}}`.
