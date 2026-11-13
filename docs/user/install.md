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

To keep evalkit out of your global tools, add it to a uv project and run it with `uv run`. The CLI, the SDK and `tracing.register` need no extra; add `server` only if you also run the server from there:

```bash
uv add "evalkit-py=={{version}}"
uv run evalkit --help
```

If you give evalkit its own uv project in the `evalkit/` folder of your agent's uv project, create it with `uv init --bare --no-workspace` and a name other than `evalkit`:

```bash
cd evalkit && uv init --bare --no-workspace --name evals && uv add "evalkit-py=={{version}}"
```

Without `--no-workspace`, uv silently adds `evalkit/` to your agent project's workspace, so it shares your agent's lockfile. `--bare` writes only `pyproject.toml`: a project named `evalkit`, or a Python package in that folder, would hide the installed `evalkit` package. Run the script from your agent project's root: `uv run --project evalkit python evalkit/run.py`.

## The SDK, into a project

`run_experiment` and `tracing.register` live in the same package as the CLI, which includes the OpenTelemetry SDK and the OTLP exporter. Pin the server's version; `uv.lock` records it:

```bash
uv add "evalkit-py=={{version}}"
```

With pip instead: `pip install "evalkit-py=={{version}}"`.

To keep evalkit out of the app's dependencies, run the eval script with it instead:

```bash
uv run --with "evalkit-py=={{version}}" python evalkit/run.py
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

They go to both `.agents/skills/` and `.claude/skills/`, created when missing, so that Claude Code (which reads `.claude/skills/`) and coding agents that read `.agents/skills/` both find them. The user docs they link to go to `evalkit-docs/` in each. `--target DIR` installs into that one directory instead.

The command prints one line per directory, for example `.claude/skills: updated 5 skills and evalkit-docs/`: `wrote` for what was not there and `updated` for what an earlier install put there. It removes a skill an earlier install put there that this version no longer ships, and names it.

Each directory holds a `.evalkit-installed` file that lists what the install put there. The command replaces and removes only what that file lists, so skills you wrote yourself in the same directory are left alone. Commit the file with the skills.

A directory that has the name of an evalkit skill (or `evalkit-docs`) and is not in that list is never overwritten. The command skips it, names it, installs the rest and exits with an error. This covers a skill of your own with the same name, and a copy of evalkit's skills that has no `.evalkit-installed` file beside it. To proceed, rename your skill, or remove the old copy, and run the command again; or install into another directory with `--target DIR`.

`skills install` also writes outside the repo: it appends each directory it wrote to `~/.evalkit/skills-targets`, so that `evalkit update` can refresh them later.

## Updating

Every API response carries `X-Evalkit-Version`. The CLI warns when it differs from the server and refuses when the major versions differ. Local runs and SDK runs refuse any difference, so after a server deploy, from the project's root:

```bash
evalkit update
```

It reinstalls the CLI at the server's version and, when `pyproject.toml` pins `evalkit-py==…`, moves the pin and runs `uv sync`. It then refreshes the skills in every directory `evalkit skills install` wrote to on this machine (listed in `~/.evalkit/skills-targets`), and in `./.agents/skills` or `./.claude/skills` if they hold an earlier install. When either of those two holds an install, it refreshes both, adding the other one if it is missing; commit the changes in repos that track the skills. When the install [skips a directory](#the-skills) in one of them, `evalkit update` still refreshes the others, then names the ones it could not fully refresh and exits with an error. Pass `--no-skills` to skip the refresh.

A CLI older than 0.3.0 cannot update itself: `evalkit update` fails with `KeyError: 'filename'`. Run the [global tool install](#the-cli-as-a-global-tool) command once instead; `evalkit update` works from then on.

A project that vendored an evalkit wheel before evalkit was on PyPI moves to PyPI once: `uv remove evalkit && uv add "evalkit-py=={{version}}"`.

Next: [Login](login.md). `evalkit login` logs in to hosted evalkit, `https://evalkit.inferedge.com`; for any other server, pass its URL: `evalkit login --url {{origin}}`.
