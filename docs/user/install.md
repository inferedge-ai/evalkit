# Install

This server runs evalkit **{{version}}**. The wheel it serves is `{{filename}}` (sha256 `{{sha256}}`). Install from the server so your client always matches it.

The server is also a Python package index at `{{origin}}/api/cli/simple`. uv's `--index` adds it next to PyPI and checks it first, so `evalkit` comes from this server and its dependencies come from PyPI.

## The CLI, as a global tool

```bash
uv tool install --reinstall evalkit --index {{origin}}/api/cli/simple
```

The same command updates it after a server deploy.

With pipx instead of uv, install the wheel file:

```bash
curl -fsSL {{origin}}/api/cli/wheel -o {{filename}} && pipx install --force ./{{filename}}
```

## The SDK, into a project

`run_experiment` and `tracing.register` live in the same package as the CLI. The `otel` extra brings the OpenTelemetry SDK and the OTLP exporter.

A project that adds evalkit as a dependency vendors the wheel, because the server serves only its current wheel, so a lock that points at the index breaks after the next deploy. From the project's root, let the CLI download the wheel into `vendor/` and check its sha256, then add it; `uv.lock` pins the file in the repository:

```bash
evalkit update --vendor vendor
uv add "./vendor/{{filename}}[otel]"
```

With pip instead: `pip install "./vendor/{{filename}}[otel]"`. Without the CLI, `mkdir -p vendor && curl -fsSL {{origin}}/api/cli/wheel -o vendor/{{filename}}` downloads the same file.

To keep evalkit out of the app's dependencies, run the eval script with it from the index instead. Nothing is locked, and each run picks up the server's current wheel:

```bash
uv run --with "evalkit[otel]" --index {{origin}}/api/cli/simple python eval/run.py
```

## Updating

Every API response carries `X-Evalkit-Version`. The CLI warns when it differs from the server and refuses when the major versions differ. Local runs and SDK runs refuse any difference, so after a server deploy, from the project's root:

```bash
evalkit update
```

It reinstalls the CLI, replaces the wheel in `./vendor` (pass `--vendor DIR` for another directory) and, when `pyproject.toml` names that wheel, points it at the new one and runs `uv sync`. It then refreshes the skills in every directory `evalkit skills install` wrote to on this machine (listed in `~/.evalkit/skills-targets`), and in `./.claude/skills` or `./.agents/skills` if they hold an earlier install; commit the changes in repos that track the skills. Pass `--no-skills` to skip that. Running the CLI install command again also updates the CLI. A `uv run --with … --index …` script needs nothing.

Next: [Login](login.md).
