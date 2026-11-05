# PyPI distribution, `evalkit init-server` and the docs site — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish evalkit to PyPI as `evalkit-py` (the only index), add `evalkit init-server` for a no-Docker local server, and publish the user docs plus an SDK API reference to GitHub Pages with versions.

**Architecture:** The wheel gains the built web UI; the server stops building and serving wheels and reports only its version, which every client pins from PyPI. `init-server` copies the bundled try kit, creates the Postgres database, writes `config.yaml` + `.env` and migrates. MkDocs Material renders `docs/user/` plus an mkdocstrings SDK page; `mike` publishes `dev` and one version per release to `gh-pages`.

**Tech Stack:** Python 3.13, uv, hatchling, typer, psycopg, FastAPI, GitHub Actions, MkDocs Material, mkdocstrings-python, mike.

**Spec:** `docs/superpowers/specs/2026-10-30-pypi-init-server-docs-site-design.md`

## Global Constraints

- Distribution name `evalkit-py`; import package and command stay `evalkit`.
- Versions: `main` → `X.Y.Z`; `develop` → `X.Y.Z.dev<run_number>`; no `+g<sha>` stamps; checkout/local image → plain `X.Y.Z`.
- No try/except except the one in `create_database_if_missing` (spec 2.3).
- Docstrings only on the public names of `evalkit.sdk`; no other new docstrings or comments.
- f-strings except stdlib logging; `X | None`; no local imports; deps via `uv add`; tooling via `uv run`.
- Docs placeholders on the site: `{{origin}}` → `http://localhost:8020`, `{{version}}` → the built version.
- MINOR bump is done by the release PR, not here; the CHANGELOG entry goes under `## Unreleased`.

---

### Task 1: Rename to `evalkit-py` and accept `.devN` versions

**Files:**
- Modify: `pyproject.toml` (`name`), `uv.lock` (via `uv lock`), `src/evalkit/version.py`
- Test: `tests/test_version.py`

**Interfaces:**
- Produces: `version.DISTRIBUTION = "evalkit-py"`; `version.parse("0.3.0.dev57") == (0, 3, 0, "dev57")`.

- [ ] **Step 1: Failing tests** — add to `tests/test_version.py`:

```python
def test_parse_dev_prerelease():
    assert version.parse("0.3.0.dev57") == (0, 3, 0, "dev57")


def test_compare_dev_builds():
    assert version.compare("0.3.0.dev57", "0.3.0.dev58") is version.VersionComparison.DIFFERENT
    assert version.compare("0.3.0.dev57", "0.3.0") is version.VersionComparison.DIFFERENT
    assert version.compare("0.3.0.dev57", "0.4.0") is version.VersionComparison.INCOMPATIBLE
```

Rename `test_parse_dev_build` to `test_parse_plain`.

- [ ] **Step 2:** `uv run pytest tests/test_version.py` → the dev test fails (`ValueError`).
- [ ] **Step 3: Implement** in `version.py`:

```python
DISTRIBUTION = "evalkit-py"
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(dev\d+))?(?:\+(.+))?")


def current() -> str:
    return importlib.metadata.version(DISTRIBUTION)


def parse(version: str) -> ParsedVersion:
    match = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"not an evalkit version: {version}")
    major, minor, patch, dev, local = match.groups()
    return ParsedVersion(int(major), int(minor), int(patch), dev or local)
```

Drop `version_from_wheel_filename` and its test (only `cli_dist` used it; removed in Task 2). Set `name = "evalkit-py"` in `pyproject.toml`, run `uv lock`, `uv sync`.

- [ ] **Step 4:** `uv run pytest tests/test_version.py` passes; `uv run evalkit --help` works.
- [ ] **Step 5: Commit** `Rename the distribution to evalkit-py and accept .devN versions`.

### Task 2: `/api/cli` reports only the version

**Files:**
- Modify: `src/evalkit/api/cli_dist.py`, `src/evalkit/api/app.py` (drop `wheel_dir`, `EVALKIT_WHEEL_DIR`, `served_wheel`), `ui/src/lib/docs.ts`, `ui/src/lib/docs.test.ts`, `ui/src/pages/DocsPage.tsx`
- Test: `tests/api/test_cli_dist.py`, tests/flows that used wheels

**Interfaces:**
- Produces: `GET /api/cli` → `{"version": str, "package": "evalkit-py"}`; `DocValues = Record<"origin" | "version", string>`.

- [ ] **Step 1: Failing test** — rewrite `tests/api/test_cli_dist.py` to keep the header tests and add:

```python
async def test_cli_info_reports_version(tmp_path, running):
    app = make_app(tmp_path)
    async with client(app) as c:
        info = await c.get("/api/cli")
        gone = [await c.get(p) for p in ("/api/cli/wheel", "/api/cli/simple/", "/api/cli/files/x.whl")]
    assert info.json() == {"version": RUNNING, "package": "evalkit-py"}
    assert [r.status_code for r in gone] == [404, 404, 404]
```

- [ ] **Step 2:** run it → fails (404 without a wheel).
- [ ] **Step 3: Implement** `cli_dist.py`:

```python
router = APIRouter(prefix="/api/cli", tags=["cli"])


@router.get("")
async def cli_info() -> dict:
    return {"version": version.current(), "package": version.DISTRIBUTION}
```

Remove the wheel plumbing from `create_app`. In the UI, `fillTemplate` handles `origin|version` only; `DocsPage` passes `{origin, version}`.

- [ ] **Step 4:** `uv run pytest tests/api tests/flows -x -q` and `cd ui && npx vitest run src/lib` pass (update `docs.test.ts`, `tests/flows/helpers_01_06.py`, `test_flow_01_install_login.py` to the new body).
- [ ] **Step 5: Commit** `Stop serving the CLI wheel; /api/cli reports the version`.

### Task 3: `evalkit update` installs from PyPI

**Files:**
- Modify: `src/evalkit/cli/update.py`, `src/evalkit/cli/main.py` (`update` command: drop `--vendor`, new docstring text)
- Test: `tests/cli/test_update.py`

**Interfaces:**
- Consumes: `GET /api/cli` from Task 2.
- Produces: `install_command(prefix: Path, requirement: str) -> list[str] | None`; `run_update(client, *, prefix=None, run=..., cwd=None, skills=True, targets_path=None, server_extra: bool = ...)`.

- [ ] **Step 1: Failing tests** — rewrite `test_update.py` around a fake server returning `{"version": REMOTE, "package": "evalkit-py"}` with `REMOTE = "0.3.0.dev58"`:

```python
def test_uv_tool_reinstalls_pinned(stale, tmp_path):
    calls = []
    run_update(client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, skills=False, server_extra=False)
    assert calls == [["uv", "tool", "install", "--reinstall", f"evalkit-py=={REMOTE}"]]


def test_server_extra_is_kept(stale, tmp_path):
    calls = []
    run_update(client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, skills=False, server_extra=True)
    assert calls[0][-1] == f"evalkit-py[server]=={REMOTE}"


def test_pipx(stale, tmp_path): ...  # ["pipx", "install", "--force", "evalkit-py==…"]


def test_unknown_installer_prints_commands(stale, tmp_path, capsys): ...  # ApiError, both commands printed


def test_project_pin_is_rewritten_and_synced(stale, tmp_path):
    (tmp_path / "pyproject.toml").write_text('dependencies = ["evalkit-py[otel]==0.2.0"]\n')
    calls = []
    run_update(client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, skills=False, server_extra=False)
    assert f'"evalkit-py[otel]=={REMOTE}"' in (tmp_path / "pyproject.toml").read_text()
    assert ["uv", "sync"] in calls


def test_vendored_wheel_prints_migration(stale, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('dependencies = ["evalkit[otel] @ ./vendor/evalkit-0.1.0-py3-none-any.whl"]\n')
    run_update(...)
    assert 'uv add "evalkit-py[otel]==' in capsys.readouterr().out


def test_already_current_still_updates_pin(...): ...
```

- [ ] **Step 2:** tests fail.
- [ ] **Step 3: Implement** `update.py` (replaces the wheel download/vendor code):

```python
PIN = re.compile(r'"(evalkit-py(?:\[[^\]]*\])?)==[^"]+"')
VENDORED = re.compile(r"evalkit-[^\s\"']+\.whl")


def install_command(prefix: Path, requirement: str) -> list[str] | None:
    parts = prefix.parts
    if "uv" in parts and "tools" in parts:
        return ["uv", "tool", "install", "--reinstall", requirement]
    if "pipx" in parts and "venvs" in parts:
        return ["pipx", "install", "--force", requirement]
    return None


def update_project_pin(cwd: Path, target: str, run) -> None:
    pyproject = cwd / "pyproject.toml"
    if not pyproject.is_file():
        return
    text = pyproject.read_text()
    if PIN.search(text):
        new = PIN.sub(lambda m: f'"{m.group(1)}=={target}"', text)
        if new != text:
            pyproject.write_text(new)
            run(["uv", "sync"])
            typer.echo(f"updated {pyproject}")
    elif VENDORED.search(text):
        typer.echo("this project uses a vendored evalkit wheel; move it to PyPI:")
        typer.echo(f'  uv remove evalkit && uv add "evalkit-py[otel]=={target}"')


def run_update(client, *, prefix=None, run=_run_checked, cwd=None, skills=True, targets_path=None,
               server_extra=find_spec("uvicorn") is not None):
    target = client.get("/api/cli").json()["version"]
    project = cwd or Path.cwd()
    update_project_pin(project, target, run)
    local = version.current()
    if local == target:
        typer.echo(f"evalkit {local} is already current")
        return
    extra = "[server]" if server_extra else ""
    requirement = f"{version.DISTRIBUTION}{extra}=={target}"
    install_prefix = prefix or Path(sys.prefix)
    command = install_command(install_prefix, requirement)
    if command is None:
        typer.echo(f'  uv tool install --reinstall "{requirement}"')
        typer.echo(f'  pipx install --force "{requirement}"')
        raise ApiError("could not tell how evalkit was installed; run one of the commands above")
    run(command)
    typer.echo(f"installed evalkit {target}")
    if skills:
        skill_targets.refresh(project, installed_cli(install_prefix), run, targets_path)
```

- [ ] **Step 4:** `uv run pytest tests/cli/test_update.py` passes.
- [ ] **Step 5: Commit** `evalkit update reinstalls from PyPI and moves the project pin`.

### Task 4: Bundle the web UI in the wheel

**Files:**
- Modify: `hatch_build.py`, `src/evalkit/paths.py` (`ui_root()`), `src/evalkit/api/app.py` (default `ui_dist`)
- Test: `tests/test_paths.py` (new), `tests/test_build.py` (new)

**Interfaces:**
- Produces: `paths.ui_root() -> Path` — `<repo>/ui/dist` in a checkout, else `<package>/bundled/ui`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_paths.py
def test_ui_root_in_checkout():
    assert paths.ui_root() == ROOT / "ui" / "dist"

# tests/test_build.py — builds a wheel in tmp and checks the UI is inside
@pytest.mark.skipif(not (ROOT / "ui" / "dist" / "index.html").is_file(), reason="ui/dist not built")
def test_wheel_bundles_ui(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=ROOT, check=True)
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    assert "evalkit/bundled/ui/index.html" in names
```

Mark `test_wheel_bundles_ui` with the existing `e2e`-style exclusion? No — it is fast enough; keep it in the default run but skipped without `ui/dist`.

- [ ] **Step 2:** tests fail (`ui_root` missing; wheel lacks the UI).
- [ ] **Step 3: Implement**

```python
# hatch_build.py, in initialize, after the skills loop
if self.target_name == "wheel" and version == "standard":
    dist = Path(self.root) / "ui" / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError("ui/dist is missing; run `npm ci && npx vite build` in ui/ first")
    for path in sorted(dist.rglob("*")):
        if path.is_file():
            build_data["force_include"][str(path)] = f"evalkit/bundled/ui/{path.relative_to(dist).as_posix()}"

# paths.py
def ui_root() -> Path:
    repo = _REPO / "ui" / "dist"
    if repo.is_dir():
        return repo
    return _PACKAGE / "bundled" / "ui"

# app.py
if ui_dist is None:
    ui_dist = ui_root()
```

- [ ] **Step 4:** `cd ui && npm ci && npx vite build`, then `uv run pytest tests/test_paths.py tests/test_build.py` pass.
- [ ] **Step 5: Commit** `Bundle the web UI in the wheel`.

### Task 5: The server CLI loads the `.env` beside `config.yaml`

**Files:**
- Modify: `src/evalkit/cli/main.py` (`_server_config`)
- Test: `tests/cli/test_server_config.py` (new)

- [ ] **Step 1: Failing test**

```python
def test_server_config_reads_env_beside_it(tmp_path, monkeypatch):
    write minimal config.yaml with judge_default.base_url: env:LLM_BASE_URL (copy the try kit's)
    (tmp_path / ".env").write_text("LLM_BASE_URL=https://llm.test/v1\nLLM_MODEL=m\nEVALKIT_ADMIN_PASSWORD=pw\n")
    monkeypatch.delenv("LLM_BASE_URL", raising=False) ...
    cfg = _server_config(tmp_path / "config.yaml")
    assert cfg.judge_default.base_url == "https://llm.test/v1"
    assert os.environ["EVALKIT_ADMIN_PASSWORD"] == "pw"


def test_environment_wins_over_env_file(tmp_path, monkeypatch): ...
```

- [ ] **Step 2:** fails. **Step 3:** `load_dotenv(path.parent / ".env", override=False)` at the top of `_server_config`. **Step 4:** passes. **Step 5: Commit** `serve and db upgrade load the .env beside config.yaml`.

### Task 6: `evalkit init-server`

**Files:**
- Create: `src/evalkit/cli/init_server.py` (prompting, kit copy, run.py pin, config/env writing; no server imports), server part in `src/evalkit/cli/server_commands.py` (`create_database_if_missing`, `init_database`)
- Modify: `src/evalkit/cli/main.py` (command), `skills/evalkit-deployment/try/run.py` (header)
- Test: `tests/cli/test_init_server.py`, `tests/test_try_kit.py` (header assertion)

**Interfaces:**
- Produces: `init_server.copy_kit(target: Path) -> None`, `init_server.pinned_requirement() -> str`, `init_server.server_config(database_url: str, data_dir: Path) -> dict`, `init_server.Answers` (dataclass: database_url, llm_base_url, llm_model, llm_api_key, admin_password), `init_server.resolve(flags: dict[str, str | None], yes: bool) -> Answers`; `server_commands.init_database(url: str) -> None`.

- [ ] **Step 1: Failing tests** (`tests/cli/test_init_server.py`):

```python
def test_flag_beats_env_beats_default(monkeypatch): ...
def test_yes_fails_without_llm_key(monkeypatch): ...  # ApiError "--llm-api-key is required with --yes"
def test_prompts_for_missing(monkeypatch): ...         # CliRunner input="\nhttps://x/v1\nm\nkey\n\n"
def test_kit_only_copies_kit_and_pins_run_py(tmp_path): ...  # agent.py, cases/, no .env, run.py pinned
def test_pin_release(monkeypatch): ...   # not editable → "evalkit-py[otel]==<current>"
def test_pin_editable(): ...             # checkout → "evalkit-py[otel] @ file://<repo>"
def test_refuses_existing_config(tmp_path): ...
def test_full_init_against_postgres(pg_url, tmp_path):  # uses the testcontainers Postgres
    url = pg_url with database "evalkit_init_<random>"
    runner.invoke(app, ["init-server", "--dir", str(tmp_path / "kit"), "--database-url", url,
                        "--llm-base-url", "https://llm.test/v1", "--llm-model", "m",
                        "--llm-api-key", "k", "--yes"])
    assert exit 0; database exists; alembic_version at head
    cfg = _server_config(tmp_path / "kit" / "config.yaml"); cfg.blob.url == f"file://{tmp_path}/kit/data"
    (kit / ".env").stat().st_mode & 0o777 == 0o600
```

Use the Postgres fixture the db tests already use (see `tests/conftest.py`).

- [ ] **Step 2:** tests fail.
- [ ] **Step 3: Implement**

`run.py` header becomes:

```python
# /// script
# requires-python = ">=3.13"
# dependencies = ["evalkit-py[otel]"]
# ///
```

`init_server.py`:

```python
KIT_REQUIREMENT = '"evalkit-py[otel]"'
LEFT_OUT = shutil.ignore_patterns(".env", "*.whl", "__pycache__", "*.pyc")


def kit_root() -> Path:
    return skills_root() / "evalkit-deployment" / "try"


def pinned_requirement() -> str:
    direct = importlib.metadata.distribution(version.DISTRIBUTION).read_text("direct_url.json")
    info = json.loads(direct) if direct else {}
    if info.get("dir_info", {}).get("editable"):
        return f"evalkit-py[otel] @ {info['url']}"
    return f"evalkit-py[otel]=={version.current()}"


def copy_kit(target: Path) -> None:
    shutil.copytree(kit_root(), target, ignore=LEFT_OUT, dirs_exist_ok=True)
    run_py = target / "run.py"
    run_py.write_text(run_py.read_text().replace(KIT_REQUIREMENT, f'"{pinned_requirement()}"'))


def server_config(database_url: str, data_dir: Path) -> dict:
    data = yaml.safe_load((kit_root() / "config.yaml").read_text())
    data["db"] = {"url": database_url}
    data["blob"] = {"url": data_dir.resolve().as_uri()}
    return data
```

`Answers`/`resolve` follow spec 2.1's `value()` pseudocode with `typer.prompt(..., hide_input=secret)`.

`server_commands.py`:

```python
def create_database_if_missing(url: str) -> None:
    parsed = sqlalchemy.engine.make_url(url)
    plain = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        psycopg.connect(plain).close()
        return
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    admin = parsed.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(parsed.database)))


def init_database(url: str) -> None:
    create_database_if_missing(url)
    run_migrations(url, "head")
```

`main.py` `init-server` command: resolve answers, `copy_kit`, stop on `--kit-only`, refuse an existing config without `--force`, `init_database`, write `config.yaml` (`yaml.safe_dump(server_config(...), sort_keys=False)`) and `.env` (0600), print next steps. A connection failure surfaces psycopg's error; the command adds the "is Postgres running?" hint by catching nothing — the hint is printed before connecting ("connecting to <url> …").

- [ ] **Step 4:** `uv run pytest tests/cli/test_init_server.py tests/test_try_kit.py` passes.
- [ ] **Step 5: Commit** `Add evalkit init-server for a local server without Docker`.

### Task 7: Release workflow and Dockerfile

**Files:**
- Modify: `.github/workflows/publish.yml`, `deploy/Dockerfile`

- [ ] **Step 1:** `publish.yml`:
  - a `version` step shared by `image` and `wheel`: `main` → `uv version --short`; otherwise → `$(uv version --short).dev${GITHUB_RUN_NUMBER}`; `uv version "$V"`.
  - `wheel`: `actions/setup-node` (24), `npm ci && npx tsc -b && npx vite build` in `ui/`, cost map, `uv build --wheel`.
  - `pypi` job: `needs: [changes, wheel]`, `if: needs.changes.outputs.code == 'true' && github.event_name == 'push'`, `environment: pypi`, `permissions: id-token: write`, download artifact to `dist/`, `pypa/gh-action-pypi-publish@release/v1` (pin by sha).
- [ ] **Step 2:** `Dockerfile`: drop the `+local` stamp, `uv build --wheel`, `EVALKIT_WHEEL_DIR`.
- [ ] **Step 3:** `docker build -f deploy/Dockerfile -t evalkit:pypi-check .` succeeds; `docker run --rm evalkit:pypi-check evalkit --version` prints the plain version.
- [ ] **Step 4: Commit** `Publish evalkit-py to PyPI from develop and main`.

### Task 8: User docs, skills, README, AGENTS, CHANGELOG

**Files:**
- Modify: `docs/user/try-locally.md`, `docs/user/install.md`, `docs/user/quickstart.md`, `skills/evalkit-usage/SKILL.md`, `skills/evalkit-deployment/SKILL.md`, `skills/evalkit-deployment/references/config-and-deploy.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`

- [ ] **Step 1:** `try-locally.md` per spec 2.6 (two "Start the server" subsections, shared steps, cleanup + failure lines for both).
- [ ] **Step 2:** `install.md`: CLI `uv tool install "evalkit-py=={{version}}"` (pipx equivalent); SDK `uv add "evalkit-py[otel]=={{version}}"`; script `uv run --with "evalkit-py[otel]=={{version}}" python eval/run.py`; Updating → `evalkit update`. Remove `{{filename}}`/`{{sha256}}`.
- [ ] **Step 3:** quickstart and skills: replace `--index …/api/cli/simple`, `/api/cli/wheel`, `--vendor` with the PyPI forms.
- [ ] **Step 4:** README "Run your own evalkit": add the pip path; AGENTS: Layout/Release/Local server lines for PyPI, `.devN`, `init-server`, docs site, the SDK docstring exception; CHANGELOG `## Unreleased` entry (spec "Compatibility").
- [ ] **Step 5:** `uv run pytest tests/test_skills.py tests/test_try_kit.py -q`; `cd ui && npx vitest run`. **Commit** `Docs: install from PyPI, try evalkit without Docker`.

### Task 9: SDK docstrings

**Files:** `src/evalkit/sdk/runner.py`, `evaluators.py`, `binding.py`, `tracing.py`, `src/evalkit/contracts/settings.py` (`JudgeSettings`, exported as `Judge`)

- [ ] **Step 1:** Google-style docstrings on `run_experiment`, `arun_experiment`, `evaluate_experiment_sync`, `aevaluate_experiment`, `evaluator`, `tracing.register`, `Fixture`, `JudgeSettings`, `RunResult`, `RunSummary`, `ExperimentResult`, describing each parameter/field from the code and `docs/user/sdk-runs.md`.
- [ ] **Step 2:** `uv run ruff check && uv run mypy src/evalkit`. **Commit** `Document the public SDK API`.

### Task 10: Docs site

**Files:**
- Create: `mkdocs.yml`, `docs/site/hooks.py`, `docs/site/sdk-reference.md`, `docs/site/stylesheets/evalkit.css`, `docs/site/overrides/main.html`, `.github/workflows/docs.yml`, `tests/test_docs_site.py`
- Modify: `pyproject.toml` (`docs` group via `uv add --group docs mkdocs-material "mkdocstrings[python]" mike`)

- [ ] **Step 1: Failing test** `tests/test_docs_site.py`:

```python
def test_nav_matches_ui_docs():
    ui = re.findall(r'from "\.\./\.\./\.\./docs/user/([\w-]+\.md)\?raw"', (ROOT / "ui/src/lib/docs.ts").read_text())
    order = [re.search(rf"import (\w+) from .*/{name}", ...)]  # map ids → files in DOC_TABS order
    nav_files = [file for section in yaml nav for file in section if file != "sdk-reference.md"]
    assert nav_files == ui files in DOC_TABS order
```

(Read `DOC_TABS` order by matching `body: <ident>` in order and mapping identifiers to their import file.)

- [ ] **Step 2:** fails (no `mkdocs.yml`).
- [ ] **Step 3:** write `mkdocs.yml` (`site_name: evalkit`, `site_url: https://inferedge-ai.github.io/evalkit/`, `repo_url`, `docs_dir: docs/user`, `theme: material` with `custom_dir: docs/site/overrides`, light/dark palettes with toggle, `font.code: JetBrains Mono`, `extra_css`, `hooks: [docs/site/hooks.py]`, `plugins: search, mkdocstrings (paths: [src], options: docstring_style google, show_source false, members_order source)`, `extra.version.provider: mike`, `markdown_extensions` admonition/pymdownx.superfences (mermaid)/tables/toc permalink), nav; `hooks.py` per spec 3.1; `evalkit.css` per spec 3.3; `overrides/main.html` with the `outdated` block; `sdk-reference.md` with one `:::` block per public name; `docs.yml` per spec 3.4.
- [ ] **Step 4:** `uv run pytest tests/test_docs_site.py` and `uv run mkdocs build --strict` pass; `uv run mkdocs serve` and look at Try locally, Install and SDK reference in light and dark.
- [ ] **Step 5: Commit** `Publish the docs and SDK reference to GitHub Pages with mike`.

### Task 11: Verify and open the draft PR

- [ ] `uv run ruff check && uv run ruff format --check && uv run mypy src/evalkit && uv run pytest -n auto`; `cd ui && npx tsc -b && npx vitest run`; `uv run python scripts/gen_schemas.py` and commit any drift.
- [ ] Manual: `uv build --wheel`, `uv tool install --force "dist/<wheel>[server]"` into a temp tool dir, `evalkit init-server` against local Postgres, `evalkit serve`, open the UI and `/docs`, run the kit's `uv run run.py --dry-run` (fails without an LLM; check it installs and connects).
- [ ] Push the branch, open a draft PR to `develop` with the PR template (TL;DR / How / Testing / Checklist), screenshots of the docs site and the server UI from the pip install.
