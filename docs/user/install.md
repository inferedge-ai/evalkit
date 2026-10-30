# Install

This server runs evalkit **{{version}}**. evalkit is on PyPI as `evalkit-py`; the package installs the `evalkit` command and the `evalkit` Python module. Install the version this server runs, so your client always matches it.

## The CLI, as a global tool

```bash
uv tool install --reinstall "evalkit-py=={{version}}"
```

With pipx instead: `pipx install --force "evalkit-py=={{version}}"`.

`curl {{origin}}/api/cli` prints the server's version, for scripts and CI:

```bash
uv tool install --reinstall "evalkit-py==$(curl -fsSL {{origin}}/api/cli | jq -r .version)"
```

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

`pip install "evalkit-py[server]"` installs the server too: the API, the web UI and migrations. [Try locally](try-locally.md) starts one on your machine; the evalkit-deployment skill covers running one for a team.

## Updating

Every API response carries `X-Evalkit-Version`. The CLI warns when it differs from the server and refuses when the major versions differ. Local runs and SDK runs refuse any difference, so after a server deploy, from the project's root:

```bash
evalkit update
```

It reinstalls the CLI at the server's version and, when `pyproject.toml` pins `evalkit-py==…`, moves the pin and runs `uv sync`. It then refreshes the skills in every directory `evalkit skills install` wrote to on this machine (listed in `~/.evalkit/skills-targets`), and in `./.claude/skills` or `./.agents/skills` if they hold an earlier install; commit the changes in repos that track the skills. Pass `--no-skills` to skip that.

A project that vendored an evalkit wheel before evalkit was on PyPI moves to PyPI once: `uv remove evalkit && uv add "evalkit-py[otel]=={{version}}"`.

Next: [Login](login.md).
