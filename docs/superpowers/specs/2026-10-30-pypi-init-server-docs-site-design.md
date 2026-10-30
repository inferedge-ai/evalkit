# PyPI distribution, `evalkit init-server` and the docs site

Date: 2026-10-30. Linear: ENG-14436 (develop builds as `.devN` pre-releases), project "Docs and Packaging".
Related, not in scope: ENG-14437 (slim the server image).

## Goal

1. Anyone can install evalkit, server included, with one `pip install` from PyPI and run it on their
   own machine without Docker.
2. One command sets up a local server against a Postgres the user already runs.
3. The user docs (`docs/user/`) and an API reference for the Python SDK (`evalkit.sdk`) are published
   on GitHub Pages, styled like the web UI, with a version selector.

There is one install path. Docker users, pip users, CI and the SDK all install evalkit from PyPI.

## Background: how distribution works today

- `uv build` produces one wheel, `evalkit-<version>-py3-none-any.whl`. It holds the CLI, the SDK, the
  server, migrations, and bundled skills, contracts and `docs/user/`. The `server` extra adds the
  server's dependencies (FastAPI, SQLAlchemy, ...); `otel` adds the OpenTelemetry SDK.
- The wheel does **not** hold the web UI. The Docker image (`deploy/Dockerfile`) builds `ui/dist`
  separately, and `create_app` (`src/evalkit/api/app.py`) finds it at `<repo>/ui/dist`.
- evalkit is not on PyPI. The image builds its own wheel into `/app/dist` and serves it
  (`src/evalkit/api/cli_dist.py`): metadata at `GET /api/cli`, the file at `/api/cli/wheel`, and a
  package index at `/api/cli/simple`. Clients install from there; `evalkit update` re-downloads it and
  can vendor it into a project's `vendor/`.
- Versions: git holds the plain semver in `pyproject.toml`. `publish.yml` stamps `X.Y.Z+g<sha>` on
  every build. Local and SDK runs refuse any version difference from the server
  (`evalkit.version.compare`).
- The PyPI name `evalkit` belongs to an unrelated project.

## Decisions

| Topic | Decision |
| -- | -- |
| Distribution name | `evalkit-py`. The import package (`evalkit`) and the command (`evalkit`) do not change. |
| Index | PyPI is the only index. The server stops serving wheels. |
| Versions | `main` builds publish `X.Y.Z`. `develop` builds publish `X.Y.Z.dev<run number>` (a PEP 440 pre-release) to the same PyPI. `+g<sha>` stamps go away. |
| Web UI | Bundled in the wheel at `evalkit/bundled/ui/`. |
| Local setup | New top-level command `evalkit init-server`, interactive like `npm init`. |
| Server `.env` | `serve`, `db upgrade` and `init-server` load a `.env` beside `config.yaml`; values already in the environment win. |
| Docs site | MkDocs Material + mkdocstrings-python, versioned with `mike`, on GitHub Pages at `inferedge-ai.github.io/evalkit`. |
| Docstrings | Added to the public names of `evalkit.sdk` only. Nowhere else. |
| Docker image | Unchanged except what this spec needs (no wheel built or served). Slimming is ENG-14437. |

## 1. Packaging and PyPI as the only index

### 1.1 Name

`pyproject.toml`: `[project].name = "evalkit-py"`. `evalkit.version.current()` reads
`importlib.metadata.version("evalkit-py")`.

### 1.2 Web UI in the wheel

`hatch_build.py` force-includes `ui/dist/**` as `evalkit/bundled/ui/**` when it builds a **standard**
wheel. An editable install (`uv sync` in a checkout) does not need it.

```python
def initialize(version, build_data):
    add skills/ as today
    if self.target_name == "wheel" and version == "standard":
        dist = root / "ui" / "dist"
        if not (dist / "index.html").is_file():
            fail: "ui/dist is missing; run `npm ci && npx vite build` in ui/ first"
        force-include every file under dist as evalkit/bundled/ui/<relative path>
```

`create_app` picks the UI directory:

```python
if ui_dist is None:
    bundled = <evalkit package dir>/bundled/ui
    ui_dist = bundled if bundled.is_dir() else <repo>/ui/dist
```

The Docker image keeps copying `ui/dist` into `/app/ui/dist` (it installs evalkit editable).

### 1.3 Versions

```
on push to main:     version = pyproject version                 e.g. 0.3.0
on push to develop:  version = f"{pyproject version}.dev{run_number}"   e.g. 0.3.0.dev57
local / checkout:    version = pyproject version (no stamp)
```

`develop` keeps the last released semver in `pyproject.toml` (feature PRs never bump it), so a dev
build sorts below the release with the same number. That is fine: every install pins an exact version,
and pip/uv only pick a pre-release when it is pinned exactly.

The image job and the wheel job of one `publish.yml` run stamp the same string, so the image's
`X-Evalkit-Version` equals the PyPI version clients pin.

The Dockerfile's `+local.<timestamp>` stamp for local builds is removed: it existed so `uv` noticed a
rebuilt served wheel, and nothing is served now. A local image and a checkout both report the plain
semver, so a developer's checkout CLI matches a locally built image.

`evalkit.version.parse` accepts `X.Y.Z`, `X.Y.Z.devN` and (for old servers) `X.Y.Z+local`:

```python
match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.(dev\d+))?(?:\+(.+))?", version)
ParsedVersion(major, minor, patch, build_id = devN or local or None)
```

`compare` is unchanged: a different MAJOR (or MINOR on 0.x) is incompatible; any other difference is
"different"; equal strings are "same".

### 1.4 What the server serves

- `GET /api/cli` returns `{"version": <running version>, "package": "evalkit-py"}` and always works
  (no wheel needed). The UI's docs pages read `version` from it.
- `/api/cli/wheel`, `/api/cli/simple/...` and `/api/cli/files/...` are removed, with `load_wheel`,
  `ServedWheel` and `EVALKIT_WHEEL_DIR`.
- The Dockerfile no longer runs `uv build` or sets `EVALKIT_WHEEL_DIR`.

### 1.5 `evalkit update`

Reinstalls the CLI from PyPI at the server's version, and updates a project's pin.

```python
target = GET /api/cli .version
spec = f"evalkit-py[server]=={target}" if the server extra is installed else f"evalkit-py=={target}"

if ./pyproject.toml has a requirement "evalkit-py[...]==<other>":
    rewrite it to "==target"; run `uv sync`; echo "updated pyproject.toml"
elif ./pyproject.toml names an old vendored "evalkit-*.whl":
    echo the migration: uv remove evalkit && uv add "evalkit-py[otel]==<target>"

if version.current() == target: echo "evalkit <v> is already current"; stop
command = uv tool  → ["uv", "tool", "install", "--reinstall", spec]
          pipx     → ["pipx", "install", "--force", spec]
          other    → print both commands; fail "could not tell how evalkit was installed"
run(command); echo "installed evalkit <target>"
refresh skills as today (unless --no-skills)
```

`--vendor` is removed: a project depends on `evalkit-py[otel]==X.Y.Z` from PyPI and `uv.lock` pins it,
so nothing breaks after a deploy until `evalkit update` moves the pin.

### 1.6 `publish.yml`

- `changes` job: unchanged.
- `wheel` job (always): set up Node 24, `npm ci && npx tsc -b && npx vite build` in `ui/`, fetch the
  cost map, stamp the version (1.3), `uv build --wheel`, upload the artifact.
- `image` job (code changed): stamps the same version as `wheel`.
- New `pypi` job (code changed, push to `develop` or `main` only): downloads the wheel artifact and
  runs `pypa/gh-action-pypi-publish` with `id-token: write` in the GitHub environment `pypi`
  (trusted publishing, no token secret).
- `github-release` (main): unchanged; attaches the same wheel.

Manual, by an admin: create the `evalkit-py` project on PyPI with `inferedge-ai/evalkit`,
workflow `publish.yml`, environment `pypi` as a trusted publisher; create the `pypi` environment.

## 2. `evalkit init-server` and "Try it locally" without Docker

### 2.1 Command

Top-level `evalkit init-server`, body in `cli/server_commands.py` (needs the `server` extra; without it
the CLI prints the `pip install "evalkit-py[server]"` hint like `serve`).

| Flag | Env | Default | Prompted |
| -- | -- | -- | -- |
| `--dir` | | `./evalkit-try` | no |
| `--database-url` | `EVALKIT_DATABASE_URL` | `postgresql+psycopg://localhost/evalkit` | yes |
| `--llm-base-url` | `LLM_BASE_URL` | none | yes |
| `--llm-model` | `LLM_MODEL` | none | yes |
| `--llm-api-key` | `LLM_API_KEY` | none | yes, hidden |
| `--admin-password` | `EVALKIT_ADMIN_PASSWORD` | `evalkit-admin` | yes, hidden |
| `--yes` | | | never prompt |
| `--force` | | | overwrite an existing `<dir>/config.yaml` |
| `--kit-only` | | | only copy the try kit; no database, config or `.env` |

```python
def value(flag, env, default, prompt):
    if flag given: return flag
    if env in os.environ: default = os.environ[env]
    if yes:
        if default is None: fail f"--{flag} is required with --yes"
        return default
    return typer.prompt(prompt, default=default, hide_input=secret)

copy_kit(dir)                                # 2.2
if kit_only: echo next steps; stop
if (dir / "config.yaml").exists() and not force: fail "… exists; pass --force"
url = value(--database-url ...); llm = value(...) ...
create_database_if_missing(url)              # 2.3
write dir/config.yaml                        # 2.4
write dir/.env (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, EVALKIT_ADMIN_PASSWORD), mode 0600
run_migrations(url, "head")
echo next steps: cd <dir> && evalkit serve, then open http://localhost:8020
```

### 2.2 The try kit and `run.py`

The kit (`skills/evalkit-deployment/try/`) is already in the wheel under
`evalkit/bundled/skills/evalkit-deployment/try/`. `copy_kit` copies it, leaving out `.env`, wheels and
bytecode, and writes `.env.example` as-is.

`run.py`'s PEP 723 header in git becomes:

```
# dependencies = ["evalkit-py[otel]"]
```

with no `[[tool.uv.index]]` and no `upgrade-package`. `copy_kit` pins it to this install:

```python
if evalkit is an editable install (direct_url.json has dir_info.editable):
    requirement = f"evalkit-py[otel] @ file://{checkout path}"
else:
    requirement = f"evalkit-py[otel]=={version.current()}"
replace '"evalkit-py[otel]"' in run.py with the requirement
```

So `uv run run.py` installs exactly the evalkit the server runs: the PyPI release or dev build, or the
checkout itself.

### 2.3 Creating the database

```python
psycopg_url = url without the "+psycopg" driver suffix
try connect(psycopg_url) → exists, close
on psycopg.OperationalError whose message says the database does not exist:
    connect to the same server's "postgres" database with autocommit
    CREATE DATABASE <name> (quoted identifier)
other connection errors propagate with the hint
    "is Postgres running? (brew services start postgresql@17, sudo systemctl start postgresql, Postgres.app)"
```

This is the only try/except, and the spec calls for it: telling "database missing" apart from other
errors is the point of the step.

### 2.4 The generated `config.yaml`

The try kit's `config.yaml` with these changes: `db.url` is the chosen URL, `db.migrate_on_start` is
dropped (init-server already migrated; `evalkit db upgrade` after an upgrade), `blob.url` is
`file://<absolute dir>/data`. The judge and `credentials` stay `env:LLM_*` references, read from `.env`.

### 2.5 `.env` beside the server config

The CLI's `_server_config(path)` calls `dotenv.load_dotenv(path.parent / ".env", override=False)` before
`load_config`. Values already in the environment win. That covers `env:` references in the config and
`EVALKIT_ADMIN_PASSWORD`. The deploy compose mounts only `config.yaml`, so deployments are unaffected.

### 2.6 Docs: `docs/user/try-locally.md`

"Start the stack" becomes "Start the server", with two subsections (plain headings, because the UI's
markdown renderer does not support MkDocs tabs):

- **Without Docker:** Python 3.13 and `uv`, Postgres 15+ running locally, the LLM settings.
  `uv tool install "evalkit-py[server]"` (or `pip install`), `evalkit init-server`,
  `cd evalkit-try && evalkit serve`.
- **With Docker:** as today. From the image the kit is copied with
  `docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/out" "$EVALKIT_IMAGE" evalkit init-server --kit-only --dir /out/evalkit-try`.

Step 1 installs the CLI from PyPI at the server's version (`uv tool install "evalkit-py==<version>"`;
the page shows `{{version}}`); the without-Docker path already has it. Steps 2–5 are shared. "Stop and
clean up" and "When something fails" get lines for both paths.

`install.md`, `quickstart.md`, the `evalkit-usage` and `evalkit-deployment` skills, `config-and-deploy.md`,
the README and AGENTS.md drop the server-index and vendoring instructions in favour of
`evalkit-py==<version>` from PyPI.

## 3. Docs site

### 3.1 Build

- `docs` dependency group: `mkdocs-material`, `mkdocstrings[python]`, `mike`.
- `mkdocs.yml` at the repo root: `docs_dir: docs/user`, nav in the UI's groups (Get started, Traces,
  Harness API, MCP) plus "SDK reference", `strict: true`.
- `docs/site/sdk-reference.md` holds `::: evalkit.sdk.<name>` blocks. It lives outside `docs/user/` so
  the UI, the wheel and `evalkit skills install` never see raw `:::` directives; a hook adds it to the
  site.
- `docs/site/hooks.py` (MkDocs hooks):

```python
on_files:          add docs/site/sdk-reference.md to the site's files
on_page_markdown:  replace {{origin}} with "http://localhost:8020"
                   and {{version}} with the version being built (env DOCS_VERSION, else pyproject)
```

- A pytest test checks that `mkdocs.yml`'s nav lists exactly the pages `ui/src/lib/docs.ts` imports,
  in the same order, plus the SDK reference.

### 3.2 SDK docstrings

Google-style docstrings on every name in `evalkit.sdk.__all__` (`run_experiment`, `arun_experiment`,
`evaluate_experiment`, `aevaluate_experiment`, `evaluator`, `tracing.register`, `Fixture`, `Judge`,
`RunResult`, `RunSummary`, `ExperimentResult`) and their fields: what each parameter means, what the
function returns, what it raises. AGENTS.md's code-style rule gains the exception: "the public
`evalkit.sdk` API has docstrings; they are its reference docs".

### 3.3 Theme

`docs/site/stylesheets/evalkit.css` maps Material's variables to `ui/src/theme.css`:

| Material | Light | Dark (`slate`) |
| -- | -- | -- |
| primary / accent | `rgb(46 103 200)` | `rgb(107 154 232)` |
| link hover | `rgb(32 72 140)` | `rgb(144 181 243)` |
| page background | `n-0` | `n-25` = `rgb(15 21 33)` |
| text | `n-800` | `n-800` dark = `rgb(241 245 249)` |
| borders, code background | `n-100`, `n-50` | dark ramp |

Body text uses the system font stack, code uses JetBrains Mono (Material's `font.code`), body size
14px. A light/dark toggle uses Material's palette switch.

### 3.4 Versions with `mike`

```
on push to develop:  mike deploy --push dev
on push to main:     mike deploy --push --update-aliases <X.Y.Z> latest
                     mike set-default --push latest
on pull_request:     mkdocs build --strict    (check only)
```

Every release adds its own version; old versions stay as published. `dev` is overwritten on each
`develop` push. Material's version selector (`extra.version.provider: mike`) lists them. An `outdated`
block in `docs/site/overrides/main.html` shows a banner on any version that is not `latest`:
"You're viewing docs for <version>. Go to latest."

Fixing an old version: `git checkout vX.Y.Z && uv run mike deploy --push X.Y.Z`.

Workflow `.github/workflows/docs.yml`, `contents: write`, `fetch-depth: 0` (mike commits to
`gh-pages`). Manual, by an admin: Settings → Pages → Source "Deploy from a branch", `gh-pages`, `/`.

## Compatibility and release

- MINOR bump. CLIs older than this ask the server for `/api/cli/wheel`, which is gone: they must
  reinstall once with `uv tool install --reinstall "evalkit-py==<version>"`. The CHANGELOG entry says so.
- Projects that vendored the wheel move to `uv add "evalkit-py[otel]==<version>"`.
- Servers must be deployed from a build that is on PyPI (any `develop` or `main` build after this lands).

## Testing

- `tests/test_version.py`: parse/compare with `.devN`.
- `tests/api/test_cli_dist.py`: `/api/cli` returns the version and package; the removed routes 404.
- `tests/cli/test_update.py`: install commands per installer, the `[server]` extra, the pyproject pin
  rewrite + `uv sync`, the vendored-wheel hint, already-current.
- `tests/cli/test_init_server.py`: value resolution (flag > env > prompt > default), `--yes` failing on
  a missing value, `--force`, `--kit-only`, the `run.py` pin (release and editable), against the
  testcontainers Postgres: creates the database, migrates it, writes a `config.yaml` that
  `load_config` accepts with the `.env` loaded.
- `tests/test_build.py`: a built wheel contains `evalkit/bundled/ui/index.html` (skipped when
  `ui/dist` is absent locally; CI's wheel job builds it).
- Docs: `tests/test_docs_site.py` for the nav check; `mkdocs build --strict` in `docs.yml`.
- `ui`: `docs.ts` values drop `filename`/`sha256`.
