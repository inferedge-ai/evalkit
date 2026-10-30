# Try evalkit locally

The try-it stack runs evalkit on your machine and walks you through the
default way to connect an agent: an **SDK run with OpenTelemetry traces**.
Your Python code calls the agent once per case. The agent's spans go to
evalkit, evalkit turns them into a trajectory, and your LLM judges the
answers. The kit includes a small sample agent that calls your LLM, so you
get a real scorecard before you touch your own agent.

It is not a deployment: it uses a demo admin password and keeps everything
on one machine. For a real deployment, follow the evalkit-deployment skill.

## What you need

- `uv` and port 8020 free.
- An OpenAI-compatible LLM endpoint (OpenAI, a LiteLLM proxy, vLLM, Ollama,
  ...) that supports tool calls and structured output, with its base URL, API
  key and model name. The sample agent and the judge both use it.
- Either Postgres 15 or newer running on this machine (without Docker), or
  Docker with Compose v2 (with Docker).

## Start the server

Pick one way. Both end with the server on http://localhost:8020 and the try
kit in a folder, and the steps after this section are the same.

### Without Docker

evalkit is on PyPI as `evalkit-py`. Install it with the server extra, then
let `evalkit init-server` set up the rest:

```bash
uv tool install "evalkit-py[server]"   # or: pip install "evalkit-py[server]"
evalkit init-server
cd evalkit-try
evalkit serve
```

`init-server` asks for the Postgres URL (default
`postgresql+psycopg://localhost/evalkit`), your LLM's base URL, model and API
key, and the admin password (default `evalkit-admin`). Each question takes
its answer from a flag or an environment variable when one is set
(`evalkit init-server --help` lists them; `--yes` never asks). It then:

- creates the database when it does not exist, and migrates it
- copies the try kit into `./evalkit-try`
- writes `config.yaml` there, with blobs stored under `evalkit-try/data`
- writes `.env` beside it, with your LLM settings and the admin password;
  `evalkit serve` reads it

Leave `evalkit serve` running and use another terminal for the steps below,
from the `evalkit-try` folder. The CLI is already installed, so skip the
install command in step 1.

### With Docker

The kit is `skills/evalkit-deployment/try/` in the evalkit repository, and the
server image holds a copy. The first `--build` takes a few minutes (it builds
the web UI and installs the server); later starts take seconds.

From an evalkit checkout, compose builds the image:

```bash
cd skills/evalkit-deployment/try
cp -n .env.example .env          # fill in LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
docker compose up -d --wait --build
```

With the server image instead, copy the kit out of it first:

```bash
export EVALKIT_IMAGE=<registry>/evalkit:<tag>
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/out" "$EVALKIT_IMAGE" \
  evalkit init-server --kit-only --dir /out/evalkit-try
cd evalkit-try
cp -n .env.example .env          # fill in LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
docker compose up -d --wait
```

Compose starts Postgres and the evalkit server, with your LLM as the judge.
`--wait` returns once the server is healthy. The server runs migrations on
every start.

### What's in the kit

| File | What it is |
| -- | -- |
| `agent.py` | The sample agent. It answers questions about Acme Widgets, a made-up company, by calling your LLM with one tool, `lookup_financials`. It records each model call and tool call as an OpenTelemetry span, using the GenAI semantic conventions. |
| `run.py` | The SDK run: `tracing.register` sends the spans to evalkit, and `run_experiment` calls the agent once per case and scores the answers. |
| `mapping.json` | The span mapping: which span attribute holds the model, the messages, the tokens and each tool call. It starts as the connect-traces skill's `gen_ai.json`. |
| `cases/acme/` | Five cases in two folders (tags), with answer rubrics and a tool check. |
| `evalkit.yaml`, `.env` | Where the cases are, and the judge's key. |
| `config.yaml` | The server's config. |
| `docker-compose.yml`, `.env.example` | With Docker only: the stack, and the template for its `.env`. `init-server` leaves them out. |

## 1. Install the CLI and log in

```bash
uv tool install --reinstall "evalkit-py=={{version}}"   # with Docker only
evalkit login --url http://localhost:8020 --username admin --password evalkit-admin
```

`{{version}}` is the server's version; `curl http://localhost:8020/api/cli`
prints it.

`evalkit login` replaces any login you already saved. To keep it, skip
`evalkit login` and point this shell at the try-it server instead (this needs
`jq`):

```bash
export EVALKIT_SERVER_URL=http://localhost:8020
export EVALKIT_TOKEN=$(curl -fsSL -X POST http://localhost:8020/api/auth/login \
  -H 'content-type: application/json' -d '{"username": "admin", "password": "evalkit-admin"}' | jq -r .token)
```

`run.py` declares its own dependencies at the top of the file, so
`uv run run.py` installs what it needs on the first run. The kit pins evalkit
to the server's version when `evalkit init-server` copies it. In an evalkit
checkout, run the kit's scripts with the checkout instead:
`uv run --project ../../.. --extra otel python run.py`.

## 2. Connect the agent's traces

Create the agent. Its trajectories come from its spans, read with
`mapping.json`:

```bash
evalkit agents create analyst --trajectory-source otel --mapping mapping.json
```

Run one case without creating an experiment:

```bash
uv run run.py --dry-run
```

It prints the answer, the session id with a count of its spans by kind and
the mapping version, and whether evalkit built a valid trajectory from them. If no spans arrived,
check that `run.py` calls `tracing.register` before the agent runs.

Then see how the mapping read that session:

```bash
evalkit traces check analyst <session id>
```

It prints one line per check: the session settled, the root span, the
mapping version, the model calls and their token totals, the tool calls
matched to the model's calls, any span attribute the mapping does not read,
and a summary of the trajectory. Every line should have a ✓.

## 3. First scorecard

```bash
uv run run.py
```

`run_experiment` syncs `cases/acme` into your own workspace (`acme@admin`),
runs the five cases, has your LLM judge each answer against its rubrics,
checks that the agent called `lookup_financials`, and prints the scorecard
and the experiment URL. The scores depend on your model. `acme-operating-margin`
asks for a figure the agent has no data for, so it passes only if the agent
says so instead of making one up.

The `answer_rubric_*` rows by rubric type (`factual_retrieval`,
`derived_computation`, `analysis_interpretation`) count only the cases that
have a rubric of that type, so their case counts differ.

Open http://localhost:8020, log in as `admin` with the admin password, and open the
experiment under the `acme` dataset. Each run shows its trajectory, the
judge's verdict on every rubric and the tool check. The Runs page lists the
raw sessions.

## 4. Iterate

This is the loop you will run on your own agent:

- Change the agent, for example the instructions in `agent.py`, and run
  `run.py` again. Compare the two experiments in the UI.
- Edit or add a case under `cases/acme/` and run again. The run syncs the
  folder first.
- Run one folder: `run_experiment(..., split="balance-sheet")`.

## 5. Connect your own agent

Do the same in your own repository:

1. **Traces.** If your agent already emits OpenTelemetry spans (OpenInference
   or GenAI conventions), keep them. If not, add an instrumentor for your LLM
   library, for example `openinference-instrumentation-openai` for the OpenAI
   client (OpenInference publishes one per popular library), or record GenAI
   spans yourself as this kit's `agent.py` does ([Tracing](tracing.md)). The
   connect-traces skill writes or checks the span mapping
   ([Mappings](mappings.md)).
2. **Setup.** Put an `evalkit.yaml` at the root of your repository, with
   `cases:` pointing at your cases directory and the judge's key under
   `credentials.judges`. That map is keyed by the judge's base URL, which must
   match the judge the run uses: the server's default judge
   (`{{origin}}/api/judge-default` shows its `base_url`), or the one you pass to
   `run_experiment(judge=...)`. This kit keys it with `env:LLM_BASE_URL` so the
   URL comes from `.env`. Keep the keys in a `.env` beside `evalkit.yaml`.
3. **Script.** Copy `run.py`. Pin evalkit in its header to your evalkit
   server's version (`evalkit-py[otel]==<version>`), call
   `tracing.register(agent="myagent")`, and write a `task` that calls your
   agent with the case input. `uv run run.py` builds an environment from the
   header alone, so add your agent's packages to its `dependencies`. If your
   agent lives in a uv project, you can instead add evalkit to the project
   (see the [Install](install.md) page) and run the script with
   `uv run python run.py`.
4. **Cases.** Add a folder under your cases directory, for example
   `mine/`, and run with `dataset="mine"`. The authoring-eval-cases skill
   covers the format. See [Concepts](concepts.md) for the terms.
5. **Agent and mapping.** Copy this kit's `mapping.json` into your
   repository, or `openinference.json` from the connect-traces skill if your
   spans follow OpenInference, and create the agent with it:
   `evalkit agents create myagent --trajectory-source otel --mapping
   my-mapping.json`. Do a dry run with your script and `evalkit traces check`
   on its session, as in step 2. For each `!` line, edit your copy, upload it
   as a draft and preview the same session:

   ```bash
   evalkit traces mapping push myagent my-mapping.json
   evalkit traces check myagent <session id> --mapping draft
   ```

   When it reads right, activate it: `evalkit traces mapping push myagent
   my-mapping.json --activate`, or use the agent's Mappings tab in the web
   UI. Runs always use the active mapping. The connect-traces skill walks a
   coding agent through the same loop.

[SDK runs](sdk-runs.md) covers the rest: agents in another process, Python
evaluators, and re-scoring. If the evalkit server
should drive your agent instead of your script (runs started from the UI,
shared agents), implement the [Harness API](harness-api.md).

## Next: run evalkit for your team

The try-it stack is for one machine. For a server your team shares, follow
the [evalkit-deployment skill](https://github.com/inferedge-ai/evalkit/tree/main/skills/evalkit-deployment):
it covers the server image, Postgres, blob storage, the judge, migrations
and the first users and workspaces. A coding agent can walk it with you.
Once the server runs, your teammates start from its own `/docs` page.

## Stop and clean up

Without Docker: stop `evalkit serve` with Ctrl-C. To delete everything, drop
the database (`dropdb evalkit`) and remove the `evalkit-try` folder.

With Docker:

```bash
docker compose down        # stop; data stays in the volumes
docker compose down -v     # stop and delete all data
```

## When something fails

- `uv run run.py` cannot find `evalkit-py==<version>`: that version is not
  on PyPI yet (a source build, or a build whose publish job has not finished).
  Run it with an evalkit checkout instead: `uv run --project <checkout>
  --extra otel python run.py`, or wait for the publish.
- `init-server` cannot connect: start Postgres (`brew services start
  postgresql@17`, `sudo systemctl start postgresql`, or Postgres.app), or
  pass `--database-url` with the user and password your Postgres needs, e.g.
  `postgresql+psycopg://me:secret@localhost:5432/evalkit`.
- `init-server` says `config.yaml` exists: you already set up that folder.
  Run `evalkit serve` there, or pass `--force` to write it again.
- `evalkit serve` says a variable is not set: check `.env` beside
  `config.yaml`.
- Compose says a variable is not set: fill in `.env` (copied from
  `.env.example`).
- Compose cannot find `evalkit:try`: outside a checkout, set `EVALKIT_IMAGE`;
  in a checkout, add `--build`.
- The server will not start: `docker compose logs server` (and
  check that `.env` is filled in: the server reads the LLM settings at start).
- The server exits with code 132 and no logs (Apple Silicon): the image is
  older than 0.22.0. Add `OPENSSL_armcap: "0"` to the server's
  `environment:` in `docker-compose.yml`, or use a newer image.
- The judge fails on every case: your endpoint must support structured
  output (`response_format` with a JSON schema). Try another model.
- `evalkit` or `run.py` talks to another server: run the `evalkit login` above
  again, or check `EVALKIT_SERVER_URL`.
