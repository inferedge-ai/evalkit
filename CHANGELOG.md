# Changelog

## Unreleased

- **Docs**: the Quickstart page (`docs/user/quickstart.md`) is a short tutorial for
  hosted evalkit: connect an agent, either by importing its history from
  Phoenix or by sending its traces straight to evalkit, mine cases from its
  real runs, run a baseline experiment and let the eval-driven-development
  skill improve the agent. What the old page covered beyond that (the Harness
  API path, hand-written cases, fixtures, comparing experiments, review and
  promotion) is on the new page Beyond the quickstart
  (`docs/user/beyond-quickstart.md`).
- **Docs**: the Install page drops the recipe for a separate uv project in
  the `evalkit/` folder; install `evalkit-py` into the agent's own project
  with pip, uv or poetry. Eval scripts in the docs and skills are
  `run_evals.py` at the project root. Framework mappings names the
  `langchain` preset for LangGraph.
- **Docs**: new skill `getting-started` walks a coding agent through the quickstart in
  the developer's agent project. `evalkit skills install` copies it with the
  others.
- **Docs**: the SDK reference is now a page in the web UI's docs (under
  Traces, after SDK runs), and `evalkit skills install` copies it to
  `evalkit-docs/sdk-reference.md` for coding agents. The docs site shows the
  same page. `scripts/gen_sdk_reference.py` writes it from the `evalkit.sdk`
  docstrings; the docs site no longer uses mkdocstrings.

- **CLI** (Fixes): Fixed every `evalkit` command crashing with `No module
  named 'bcrypt'` in a project that has uvicorn and SQLAlchemy but not the
  server extra. The CLI now loads `serve`, `db upgrade`, `db rekey` and
  `init-server` only when every package of `evalkit-py[server]` is
  installed, and otherwise says to install the extra. `evalkit update` uses
  the same check to decide whether to reinstall with `[server]`.

- **Scorecard**: errored runs show in every scorecard (ENG-14621). A run is
  errored when it ended without completing with an answer: it has no `output`,
  and it either did not end `completed` or has no `answer`. An errored run
  gets no score. A case is errored when every run of it errored, and partly
  errored when only some repetitions did; a partly errored case is scored on
  the runs that completed. The report (`GET /api/experiments/{id}/scorecard`)
  has new fields `selected_cases`, `errored_cases`, `partly_errored_cases`
  and `bucket_cases`, each case row has `errored_runs`, and an experiment's
  `progress` has `errored` and `partly_errored`. `evalkit summary` (with or
  without `--baseline`) and the SDK's printed scorecard start with `cases: 6,
  3 errored (no run completed with an answer; left out of every score)`, or
  `cases: 2 of 3 ran` when not every selected case has a run, and add `(3 of
  6 cases)` to a bucket that covers fewer cases than the experiment selected.
  `evalkit experiments list` puts `3 of 6 cases errored` before the bucket
  scores, and `evalkit status` adds `3 errored`. The web UI shows the same
  counts above the bucket cards, on each bucket card (`2 of 3 cases scored`,
  and `1/3` beside an evaluator that scored fewer cases), on the compare page
  and in the cases column of the experiments list. On the experiment page a
  case with errored runs gets an `errored` or `partly errored` badge and
  counts as failing, whether or not its runs carry error text. `evalkit
  summary` lists error text under `run and evaluator errors:`.
- **Scoring**: which runs are scored follows the run's status and answer, not
  its error text. A `completed` run that has an answer and also carries a
  non-fatal `error` is now scored; before, it was left out unless it had an
  `output`. A run with no error text that did not end `completed`, or ended
  with neither `answer` nor `output`, is no longer scored on an empty answer.
  `evalkit evaluate <id>` scores the runs of an earlier experiment that the
  old rule left out.
- **Runs**: `evalkit runs list` and the Runs page show a status and a
  duration for every run. An OTel session is `failed` when a root span has
  status `ERROR` or an `error.type` attribute. Otherwise it is `running`
  until it settles (no new span for `otel.settle_seconds`, or the agent's
  `import_settle_seconds` for a trace source) and `completed` after that. Its
  duration runs from its first span's start to its last span's end. A trace
  without a session gets its status and duration the same way. The run
  list API has a new field `duration_ms`.

- **CLI**: `evalkit skills install` without `--target` writes the skills and `evalkit-docs/` into both `.agents/skills/` and `.claude/skills/`, creating either when missing, so Claude Code finds them in a repo with no `.claude/` folder. Before, it wrote only to `.claude/skills/` when `.claude/` existed, else `.agents/skills/`. `--target DIR` still writes to that one directory. The command prints one line per directory (`wrote 5 skills and evalkit-docs/` for what was not there, `updated …` for what an earlier install put there), and names each skill it removed because an earlier install put it there and this version no longer ships it. It no longer overwrites a directory that has the name of an evalkit skill (or `evalkit-docs`) but is not listed in that directory's `.evalkit-installed`: it skips it, names it, installs the rest and exits 1. Rename or remove that directory and run the install again, or pass `--target DIR`. This also applies to a copy of evalkit's skills with no `.evalkit-installed` beside it. `evalkit update` refreshes both folders, carries on past a folder where the install skipped something, then names those folders and exits 1, and adds the missing one to a repo that holds an earlier install in only one of them (ENG-14623).
- **CLI**, **SDK**: `run_experiment`, `evaluate_experiment`, `evalkit run`, `evalkit evaluate` and `evalkit summary` end with `link: <server>/experiments/<id>`, the experiment's page in the web UI. `evalkit status <id>` prints the same link under the status line. A server run prints `experiment <id>` and then the `link:` line when it starts, where it used to print `experiment <id>: <url>`. `evalkit experiments list` ends with `evalkit status <id> shows one experiment and its link`. A dry run prints no experiment link.
- **CLI**, **SDK**: commands that show, create or change one item with a page in the web UI end with `link: <url>` too. Datasets (`/datasets/<ref>`): `evalkit sync`, `evalkit run` when it syncs, `evalkit datasets create`, `evalkit datasets show`. Cases (`/datasets/<ref>/cases/<id>`): `evalkit review show <dataset> <case-id>`, `evalkit review approve` (one link under each case), `evalkit review request-changes`, `evalkit review comment`, `evalkit review resolve`. Agents (`/agents/<ref>`): `evalkit agents create`, `edit`, `show`, `archive`, `restore`. Agent runs (`/runs/<agent>/<run id>`): `evalkit traces check`, `evalkit traces import` (the checked session), `evalkit runs pull --run-id`; `evalkit runs list --json` adds a `url` field to each run. A personal agent with a harness gets neither, because the server cannot reach its runs. `evalkit runs pull --experiment <id>` ends with the experiment's link. `evalkit sync` prints the dataset link last, after its review lines. `evalkit datasets list` and `evalkit agents list` end with a line naming the show command, like `evalkit experiments list`; `evalkit datasets list <dataset>`, `evalkit review show <dataset>` and `evalkit review queue` end with `evalkit review show <dataset> <case-id> shows one case and its link`. In the SDK, `RunSummary` has a new `url` field (the experiment's page for its case, or after a dry run the agent run's page), and a dry run prints `  link: <url>` for each case whose session the server collected (not for a pushed trajectory, which a dry run does not store). The URL builders live in `evalkit.client.links`. Remote evaluators, workspaces, trace sources and LLM providers have no page of their own and print no link.
- **Web UI**: the docs pages, a case's question, a run's answer, agent steps
  in a trajectory and Markdown files in a run's workspace share one Markdown
  look, at 14px. Code blocks have a header with their language and a copy
  button, and syntax highlighting everywhere, not only in the docs; a block
  over 20,000 characters shows as plain text. Tables scroll sideways in a
  bordered frame. Links in answers, trajectories and workspace files open in
  a new tab; footnote links stay on the page. Maths renders where it did
  before: questions, answers and agent steps. User steps in a trajectory are
  14px to match.
- **Web UI**: signed-out pages no longer have evalkit's bar across the top.
  The docs page carries its own header instead: the evalkit logo, the theme
  switch and Sign in, above the docs menu. The sign-in page is unchanged.
- **Web UI**, **Docs**: evalkit has a new logo, a `/e` mark beside
  "evalkit" in lowercase mono. It replaces the "Evalkit" wordmark in the app
  sidebar, the sign-in card and the docs page, the favicon (now also a
  `favicon.ico`), the docs site's header and the README.
- **Extending evalkit** (before you upgrade): a `landingPage` and
  `publicRoutes` render with no evalkit bar for a signed-out visitor, inside
  a plain scroll container that fills the window, so a package must draw its
  own header and way home on them. `useOptionalMe`, `useTheme`, `Lockup`
  (the logo) and `/favicon.svg` are now stable names.
- **CLI**, **SDK**: the `env:` references in `evalkit.yaml` and `evalkit run --judge-api-key-env` read only the environment. The CLI and SDK no longer read the `.env` beside `evalkit.yaml` (or in the current directory). Export the variables in your shell instead. `evalkit serve` still reads the `.env` beside `config.yaml`.
- The user docs support tabs (`<!-- tabs -->` … `<!-- /tabs -->` around
  `####` headings), anchors on table rows (`<!-- anchor-rows -->` before a
  table) and images in `docs/user/images/`, in the web UI's docs page and on
  the docs site. In the web UI's docs page, headings get the same ids as on
  the docs site, and the page scrolls to a `#fragment` target (heading or
  row) on load and when the hash changes. The web UI bundles the images;
  `evalkit skills install` and the wheel's `evalkit/bundled/docs/` leave
  `images/` out. Concepts has rows for App run, Trace
  source, Preset and Hosted evalkit; see CONTRIBUTING.md for the markers.
- The docs site uses the web UI's design tokens: the build generates
  `stylesheets/tokens.css` from `ui/src/styles/tokens/`, and the site's
  colours (light and dark), Roboto and JetBrains Mono fonts, radii, header,
  sidebar, search, tables, code blocks, content tabs and version
  selector follow them. The web UI's docs page sets its headings, images and
  tables from the same tokens, and its mobile page menu is the shared
  `Button`.
- Experiments can take trajectories from the agent's own trace store. Set
  `--trace-source` and `--project` on an OTel agent (`evalkit agents
  create/edit`, or the agent form); SDK runs and harness runs then import each
  run's session from that store, with no `tracing.register` and no agent
  token. The source and project are checked when they are saved and when an
  experiment starts. An agent that runs in the task's process needs no
  change; any other agent takes `metadata["session_id"]` or the task returns
  `RunResult(session_id=...)`. New agent settings `trace_source`, `project`,
  `import_settle_seconds` (15) and `import_wait_seconds` (120), with CLI flags
  `--import-settle`, `--import-wait` and `--clear-trace-source`.
  `TraceSourceClient` gains `check_project`.
- `source` and `project` on `POST /api/agents/{agent}/sessions/import` and
  `POST /api/agents/{agent}/imports` (and `evalkit traces import
  --source/--project`) are optional and default to the agent's. A
  single-session import that leaves both out (a pull from the agent's own
  source) needs only permission to run the agent, not write access, and
  waits at most 30 s for the store. A trace source that refuses its key when
  the agent is saved or an experiment starts answers 400 naming
  `evalkit trace-sources edit <name> --api-key-env`.
- A session that holds only evalkit's `evalkit.run` span is now `unavailable`
  with a reason, on the collector path too, instead of `valid` with an empty
  trajectory. A run whose trajectory is partial or unavailable, or whose
  mapping read no LLM or tool steps, records why in the new
  `agent_runs.trajectory_note` (migration `0019`): in the experiment's runs,
  on the run page, in `RunSummary.trajectory_note` and on the progress line.
  A refetch that finds the trajectory replaces the note.
  The session read returns `only_run_span` and `reason`.
- An SDK run with no tracer provider whose task neither takes `metadata` nor
  returns a session id now fails at once with a message naming the fix,
  replacing `no tracer provider configured and the task returned no
  session_id`.
- The span upsert leaves an unchanged span's row, `received_at` included,
  untouched; a session id fix alone no longer moves `received_at`, and an
  empty incoming session id never replaces a stored one.
- Phoenix imports put `openinference.span.kind` back on a span when Phoenix
  returns it only in its `span_kind` field. Some Phoenix servers move it out
  of the attributes, and the `openinference` mapping then saw no LLM or tool
  spans.
- An unknown trace source now reads `trace source X not found; evalkit
  trace-sources list shows the registered ones`.
- New optional setting `auth.oidc.cli_client_id`: an OAuth client of
  `evalkit login`'s own, for identity providers that skip the approval step
  for evalkit's web client. When it's set, `evalkit login` uses it for the
  browser sign-in, the device flow, the code exchange and later refreshes, and
  the server accepts an ID token issued to either client. Unset, it defaults
  to `client_id` and nothing changes. `GET /api/auth/providers` returns it in
  `oidc.cli_client_id`. An older CLI still signs in with the web client, so
  people need `evalkit update` before an operator removes the web client's
  loopback redirect. `evalkit login` against a server it can't talk to now
  says to run `evalkit update --server <url>`.
- New server setting `auth.oidc.ui_resources`: other APIs the web UI signs
  in for, so pages a package extending evalkit adds can call them as the
  signed-in person. The web UI's sign-in names `authorize_params.resource`
  first and these after, as repeated `resource` parameters. Its code exchange
  and its own refreshes then name `authorize_params.resource`, so evalkit's
  calls keep a token for evalkit's API. The setting needs
  `authorize_params.resource`; the server refuses to start without it.
  `GET /api/auth/providers` returns it in `oidc.ui_resources`. `evalkit login`
  doesn't send it. A page gets a token for one of these APIs with
  `getApiToken(resource)` from `@/auth/apiToken`.
- On every server with single sign-on, the web UI now gives up on the
  identity provider after 10 seconds. This covers discovery, the code
  exchange at sign-in and refreshes. Before, these had no time limit: 10
  seconds is oidc-client-ts's own default for refreshes, which its silent
  refresh skipped. An identity provider slower than that now fails sign-in or
  refresh instead of hanging. oidc-client-ts is pinned to exactly 3.5.0.
- New `GET /api/mapping-presets` returns the span mapping presets the package
  ships, as `[{"name", "document"}]`, to any signed-in user. In the web UI, the
  new-agent and edit-agent forms have a preset picker above "Span mapping": a
  pick fills the field with the preset's JSON, which stays editable. When the
  pick replaces text you typed or pasted, the form says so and offers Undo,
  which brings that text back even after several picks.
- A package extending evalkit's UI can add pages signed-out visitors open and
  a page for `/`. `UiExtensions` has two new required fields, so a wrapper's
  `ui/src/extensions/index.tsx` must set both:
  - `publicRoutes` render under the public navbar for a signed-out visitor
    and inside the app shell for a signed-in person, as `/docs` does.
  - `landingPage` (or `null`) is what a signed-out visitor sees at `/`;
    signing out then lands on `/` instead of `/login`. A signed-out deep link
    still goes straight to sign-in.
  - The new `SignInLink` (`@/auth/SignInLink`) starts sign-in at once, even
    right after a sign-out; the public navbar's Sign in button uses it, so
    it no longer stops at "You're signed out · Sign in again".
  - `/login?prompt=<value>` passes `prompt` to the identity provider's
    authorize request, e.g. `prompt=create` for its sign-up form.
  - While evalkit hands sign-in to the identity provider, the page says
    "Signing you in…" instead of "Taking you to <provider>…".
- With `auth.password_login: none`, evalkit makes no password accounts:
  `POST /api/users` with a password (`evalkit users add <name>`) and
  `POST /api/users/{name}/password` (`evalkit users set-password`) answer 400.
  Before, they made accounts that could never sign in. Machine users
  (`evalkit users add <name> --subject <sub>`) are added as before.
- With `auth.password_login: none`, the web UI has no Users page: Settings ›
  Organization and ⌘K search no longer list it, and `/settings/users` shows
  the not-found page. Every person comes from the identity provider, so the
  page's actions did not stick. `evalkit users list`, `evalkit users archive`
  and `evalkit users add <name> --subject <sub>` work as before.
- With `auth.oidc.admin_claim` set, `POST /api/users/{name}/promote`
  (`evalkit users promote`, **make admin** on the Users page) answers 400 for a
  person bound to the identity provider (a user with a subject), whether or not
  they also have a password: each sign-in through the identity provider sets
  their admin flag from the claim, so a promotion by hand was lost at their
  next one. `POST /api/users/{name}/demote` (`evalkit users demote`) still
  works for them: evalkit reads the claim at sign-in, not when a token renews,
  so demote is how an admin removes admin before that person next signs in,
  and it lasts while the identity provider agrees. Password accounts with no
  subject and machine users are unchanged.
- The `otel` extra is gone: `opentelemetry-sdk` and
  `opentelemetry-exporter-otlp-proto-http` are now base dependencies of
  `evalkit-py`, so `evalkit.sdk.tracing.register` and SDK runs with traces work
  on a plain `uv add evalkit-py`. `evalkit-py[otel]` no longer names an extra:
  pip and uv still install the package but warn about an unknown extra, so drop
  `[otel]` from your pins. The base install, the CLI included, now also pulls in
  the OpenTelemetry SDK, the OTLP HTTP exporter and what they need
  (`opentelemetry-proto`, `protobuf`, `requests` and others). `evalkit
  init-server` writes `evalkit-py==<version>` into the try-it kit's `run.py`,
  and the vendored-wheel hint from `evalkit update` names `evalkit-py==<version>`.
  The `evalkit tracing needs the otel extra` error no longer exists.
- `evalkit login` no longer asks for a server URL. Without `--url` it logs in
  to hosted evalkit, `https://evalkit.inferedge.com`, and first prints
  `logging in to https://evalkit.inferedge.com; pass --url to log in to another
  server`. Self-hosted users must pass `--url <url>`; before, a bare
  `evalkit login` prompted `Server URL`. The "no server configured" and old
  profiles-file errors now name both forms. `EVALKIT_SERVER_URL` works as
  before and does not change where `evalkit login` logs in.
- The span mapping presets now ship in the package, so creating an OTel agent
  no longer needs `evalkit skills install` first. `--mapping` on
  `evalkit agents create` and `evalkit agents edit`, and the mapping argument
  of `evalkit traces mapping push`, take a preset name (`openinference`,
  `gen_ai`, `langchain`, `llamaindex`, `crewai`, `google-adk`, `mastra`,
  `agno`, `claude-agent-sdk`) or the path of a mapping JSON file. A value that
  names an existing file is read as the file; otherwise it must be a preset
  name, compared exactly (`gen_ai`, not `gen_ai.json`). Anything else is
  refused with a message that lists the presets; before, a missing file was a
  usage error with exit code 2, now it exits 1.
- New command `evalkit traces mapping presets [NAME]`: with no argument it
  lists the preset names, with a name it prints that preset as JSON, to save,
  edit and push as a draft
  (`evalkit traces mapping presets langchain > eval/span-mapping.json`).
- The preset JSON files moved from the connect-traces skill's `references/`
  folder to `src/evalkit/core/traces/mapping_presets/`, the only copy.
  `evalkit skills install` no longer copies them, and removes the old copies
  from a folder it installed into before. The try-it stack's `mapping.json`
  is gone: its walkthrough creates the sample agent with `--mapping gen_ai`.
  The connect-traces skill and the user docs now use presets by name.
- `evalkit.yaml` is optional. Without one, the CLI and `run_experiment` act as
  if the file sat in the current directory with `cases: evalkit/cases`,
  `fixtures: evalkit/fixtures` and no credentials, and read the `.env` there. A
  file that leaves out `cases` or `fixtures` gets the same defaults, relative
  to its own directory. So `evalkit sync <dataset>`, `evalkit run <dataset>`
  and `run_experiment` work with only `evalkit/cases/<dataset>/` on disk. Before,
  `evalkit run` and the other commands that drive a personal agent failed with
  `no evalkit.yaml found above the current directory`, `run_experiment` raised
  `FileNotFoundError`, and a bare dataset name needed `cases:` to be set.
  Different from before:
  - A missing dataset directory fails with one message that names the cases
    directory it looked in and says to put the cases in
    `<cases directory>/<dataset>/`, set `cases:` in `evalkit.yaml`, or pass
    the dataset's directory.
  - A sync always looks in the fixtures directory for bytes the server does
    not hold. Without `fixtures:` it used to skip the upload, and the server
    answered `E-FIXTURE-MISSING`; now the CLI fails first and names the file
    it looked for.
  - With no file or no `cases:`, a path under `evalkit/cases/<dataset>/` now
    syncs into `<dataset>` (`evalkit sync evalkit/cases/finance/statements`
    patches `finance`); it used to become a dataset named after the folder.
  - `run_experiment` on a dataset in your workspace whose directory is
    missing prints the path it looked in, then runs the copy the server
    holds.
  - `cases:` or `fixtures:` set to an empty value (null) in `evalkit.yaml`
    fails to load; leave the key out instead.
  - A sync refuses cases or fixtures that sit inside a folder named
    `evalkit` holding an `__init__.py`: that folder is a Python package and
    would hide the installed `evalkit` package from `import evalkit`. The
    error says to delete the file or set `cases:` and `fixtures:` to another
    folder. Commands that sync nothing from disk are not affected.
- The web UI's sign-in pages (`/login` and `/login/callback`) no longer show
  the public navbar, so they have no theme switch. They use the theme last
  chosen in that browser (the system's on a browser's first visit); to change
  it, use the switch on any other page. The card shows the Evalkit wordmark
  centred at its top; when a package extending evalkit sets a landing page,
  the wordmark links to `/`, so a visitor can go back to it. `/login` has a
  footer: **Docs** opens the in-app docs
  at `/docs`, **GitHub** opens the repository in a new tab, and the version
  the server runs (`evalkit-py v0.5.0`, from `GET /api/cli`) opens that
  version's GitHub release in a new tab. A development build (`0.5.0.dev12`)
  shows its version without a link, and when `GET /api/cli` fails the footer
  leaves the version out. The footer reads the version without signing in, so
  it shows even while the identity provider is unreachable. Every other
  signed-out page, such as the docs and a wrapper's landing page and public
  routes, keeps the navbar.

## 0.5.0

_Released 2026-11-11._ A redesigned web UI, sign-in through your identity
provider alone, and finer control over which private addresses a server may
call.

### Before you upgrade

- **CLI:** Run `evalkit update --server <url>` once the server is on 0.5.0;
  a 0.4 CLI refuses every other command against it.
- **Server:** `GET /api/meta` no longer returns `accent_color`; read it from
  the new public `GET /api/appearance`.
- **Extending evalkit:** `UiExtensions` drops `moreNav` and `adminNav`; move
  those pages to `accountSettings` (everyone) or `organizationSettings`
  (admins), named in lowercase letters, digits and dashes, and not
  `workspace`, `users`, `workspaces`, `trace-sources` or `llm-providers`
  (evalkit drops a page whose name breaks this or is already taken).
- **Extending evalkit:** evalkit's CSS classes for buttons, badges, inputs,
  cards, tabs, form rows, checkboxes and loading placeholders (`btn`, `chip`,
  `input`, `card`, `card-head`, `tab-button`, `form-row`, `form-label`,
  `check`, `skeleton`) are gone, so a page that uses them moves to the shadcn
  components. Dark mode is the `dark` class on `<html>` instead of
  `data-theme="dark"`.

### Features

- **Sign-in:** `auth.password_login: none` sends a signed-out visitor
  straight to your identity provider, refuses every password and needs no
  `EVALKIT_ADMIN_PASSWORD`. It requires `auth.oidc.admin_claim`, so the
  provider says who is an admin, and signing out stops every tab at
  **Sign in again** rather than signing you straight back in.
- **Server:** `trusted_endpoints` lists `http` or `https` origins that every
  private-address check lets through, for a service every tenant may reach,
  such as an in-cluster LLM gateway that checks its own credentials.
- **Web UI:** A Workspaces page (Settings › Organization) lists every
  workspace with its member count and promotion link, and admins create
  shared workspaces there. `GET /api/workspaces` returns `members`.
- **Extending evalkit:** A package serving evalkit can decide the
  private-address rule per request, for example per tenant, by replacing
  `get_allow_private_endpoints` from `evalkit.api.deps`.

### Improvements

- **Web UI:** A new design: a left sidebar that collapses to icons (⌘B) and
  opens as a sheet on a phone, Roboto at 14px in new light and dark colours,
  and new buttons, forms, dialogs and icons. Every page is laid out again,
  with loading placeholders shaped like what loads, empty pages that say what
  to run, and errors shown where the data failed.
- **Web UI:** One Settings area, opened from the account menu, holds the
  current workspace's settings and, for admins, Users, Workspaces, Trace
  sources and LLM providers. Old URLs redirect.
- **Web UI:** Tables let you resize, reorder, sort and hide columns and
  filter text columns, and each table remembers its layout in the browser.
  Layouts saved before 0.5.0 are dropped once.
- **Web UI:** A page you come back to shows its last data at once and
  refreshes in the background, and a save, archive or comment shows without
  a reload.
- **Web UI:** A deploy's `server.accent_color` draws a strip along the top of
  every page, sign-in included, instead of recolouring buttons and links.
- **Web UI:** The reviews queue you picked is in the URL
  (`/reviews?view=all`), so Back from a case returns to it.
- **Sign-in:** A first SSO sign-in whose name another SSO user, an archived
  account or a workspace already has gets the next free number (`ann1`,
  `ann2`) instead of a 409. A name held by an active password account not yet
  linked to SSO still gets the 409, which names the subject for
  `evalkit users bind`.
- **Sign-in:** Usernames match in any case: `evalkit users add Bob` creates
  `bob`, and the password login and the user and member commands accept
  `Bob`.

### Fixes

- **Sign-in:** Fixed a first SSO sign-in refusing anyone whose email or
  `preferred_username` has a `+` or another character outside lowercase
  letters, digits, `.`, `_` and `-`. Usernames now take any character except
  whitespace, `@`, `/`, `\`, `?`, `#` and `%`, and can't be just `.` or `..`.
- **Sign-in:** Fixed a signed-out visitor or an expired password session
  getting an error instead of the login page while the identity provider is
  unreachable, so an admin can still sign in with a password.
- **CLI:** Fixed `evalkit lint` and `evalkit sync` refusing a case file with
  top-level `x-` keys (such as `x-shared`, to hold YAML anchors), broken in
  0.4.0. The server parses these files, so the fix needs a 0.5.0 server.
- **Web UI:** Fixed a file on a run's Workspace tab failing to load with no
  reason, or just `Error`: it shows the server's message, or `HTTP <status>`.
  The run file endpoints answer 502 `harness unreachable` instead of a
  plain-text 500 when the harness is down.
- **Web UI:** Fixed an API error with no message, such as a proxy's 502
  page, showing as an empty error; it shows `HTTP <status>`.
- **Web UI:** Fixed a score named like a JavaScript object property (such as
  `constructor`) breaking the page that shows its ⓘ.
- **Web UI:** Fixed GHSA-238p-pmpm-9mq7 (KaTeX below 0.18.2 reads polluted
  object prototypes) by using KaTeX 0.19.1 throughout.
- **Extending evalkit:** Fixed trace import checking private addresses
  against the server's own config instead of a package's replaced
  `get_config`.

## 0.4.0

_Released 2026-11-07._ Single sign-on through any OIDC identity provider,
judge-using evaluators scored on the server, packages that extend evalkit,
and an Apache-2.0 licence (was AGPL-3.0).

### Before you upgrade

- **CLI:** Run `evalkit update --server <url>` once the server is on 0.4.0;
  a 0.3 CLI refuses every other command against it.
- **Server:** Stop the old server, then run `evalkit db upgrade` before
  starting the new one. One migration fails if the default judge's base URL
  matches no LLM provider: register one or clear the default judge first.
- **Server:** Each server now opens up to 30 database connections
  (`db.pool_size` 20 plus `db.max_overflow` 10). Keep that times your
  replicas well under Postgres `max_connections`, or lower them in
  `config.yaml`.
- **Server:** The server config no longer takes `mcp.oauth`; a config that
  has it fails to load. Configure `auth.oidc` instead.
- **Server:** Trace sources are checked against private addresses like LLM
  providers. Without `allow_private_endpoints: true`, a source on a private
  address stops importing until an admin points it at a public one.
- **Server:** A proxy in front of the server must let
  `POST /api/experiment-runs/<id>/server-evaluations/<name>` run for
  minutes; the CLI and SDK wait up to 900 s
  (`EVALKIT_SERVER_EVALUATOR_TIMEOUT`).
- **Server:** A harness that declares `run_listing` names the tenant
  `org_id` instead of `client_id`, in the `GET /runs` filter and on each
  run. The run browser's filter and the MCP `find_runs` tool say `org_id`
  too.
- **Evaluators:** The default judge is set by provider name: `GET`, `PUT`
  and `PATCH /api/judge-default` use `provider`, and a `base_url` judge is
  refused there and on server runs.
- **Evaluators:** `evalkit judge-default set --concurrency` is gone and
  `Judge(concurrency=...)` is ignored; set `evaluation_concurrency` instead.
- **SDK:** `evalkit.yaml` no longer takes `credentials.llm_providers` (or
  `credentials.judges`); a file that has it fails to load. Pass the judge key
  with `--judge-api-key-env` or `Judge(api_key=...)`.
- **SDK:** "Split" is "tag" everywhere: `run_experiment(split="x")` is
  `run_experiment(tags=["x"])`, the API's `selection` takes `tags` (a request
  with `tag` is refused), and `GET /api/datasets/{ref}/splits` is
  `GET /api/datasets/{ref}/tags`.
- **SDK:** Code that embeds `evalkit.sdk` installs `httpx2` instead of
  `httpx`, and `SyncApi` and `AsyncApi` take an `httpx2.Auth`
  (`evalkit.client.oidc.static_auth(token)` wraps a token).
- **CLI:** `evalkit run finance/<name>` no longer falls back to a tag when
  `<name>` is not a folder or file of the dataset: pass `--tag <name>`.
- **CLI:** `evalkit traces mapping push --activate` is gone, and so is the
  `activate` query parameter on `PUT /api/agents/{agent}/mappings/draft`:
  push the draft, then run `evalkit traces mapping activate <agent>
  <version>`.
- **CLI:** A case file with `cases:` or `overlays:` refuses any other
  top-level key besides `defaults`, where it used to ignore it (a typo such
  as `defualts:` dropped your defaults silently), and both must be lists.
- **Tracing:** `tracing.register`, `tracing.exporter` and
  `tracing.processor` need `EVALKIT_OTEL_TOKEN` (an agent token); they no
  longer fall back to a login token.
- **Tracing:** Sibling agents that hand over without a tool call (CrewAI
  sequential tasks, say) now make one trajectory; list them in the mapping's
  `subagent_spans` to keep them apart.
- **Packaging:** Only releases are published to PyPI; get a `develop` build
  (`X.Y.Z.devN`) from the workflow artifact or the GHCR image.
- **Extending evalkit:** `create_app` no longer takes `mcp_jwt_verifier`;
  `/mcp` verifies tokens with the `auth.oidc` settings.

### Features

- **Sign-in:** Single sign-on through any OIDC identity provider that
  issues JWT access tokens (`auth.oidc`): the web UI offers "Continue with
  <provider>", `evalkit login` opens the browser (or uses the device flow
  with `--no-browser`), and the first sign-in creates the user.
  `auth.password_login` (`admin` or `all`) decides who may still use a
  password.
- **Sign-in:** CI signs in as a machine client with `EVALKIT_OIDC_CLIENT_ID`
  and `EVALKIT_OIDC_CLIENT_SECRET`; an admin adds it with
  `evalkit users add <name> --subject <sub>`, and `evalkit users bind` links
  an existing user to an identity.
- **Sign-in:** `auth.oidc.admin_claim` lets the identity provider grant and
  revoke evalkit admin at each sign-in, and `auth.oidc.required_scopes`
  refuses access tokens that lack a scope.
- **Evaluators:** Local and SDK runs score the judge-using built-ins
  (`answer_rubrics`, `presentation_judge`, `trajectory_rubrics`) on the
  server with its LLM provider, so they need no judge key; each score
  records where it ran. To bring your own judge, pass `--judge-base-url` and
  `--judge-api-key-env`, or `Judge(base_url=..., api_key=...)`.
- **Extending evalkit:** Another package can serve evalkit with API routes
  of its own (`app.include_router()` after `create_app`, behind sign-in with
  `SIGNED_IN`) and UI pages of its own (`ui/src/extensions/`), and can split
  the data by organisation with `create_app(resolve_tenant=...)`, an async
  function that returns the tenant id or `None` to refuse the request. See
  "Extending evalkit" in `AGENTS.md`.
- **CLI:** `evalkit run --tag` takes several tags and runs the cases
  carrying any of them; the run dialog's Tags is a multi-select, and an
  experiment's case table filters by tag.
- **CLI:** `evalkit traces mapping activate <agent> <version>` makes a
  pushed draft live, or rolls back to an older version.
- **CLI:** New commands `evalkit experiments list` and
  `evalkit datasets show <dataset>`: the first filters by `--agent`,
  `--dataset` and `--limit`; the second prints a dataset's last sync, case
  counts per review state and tags.
- **Review:** `evalkit review approve` takes several cases or
  `--all-waiting`, `evalkit review queue finance@dev` lists one dataset's
  cases, and `evalkit review resolve <comment-id>` needs only the comment id.
- **Web UI:** Datasets in a personal workspace have Run experiment too; set
  the options and copy the `evalkit run` command it builds.
- **SDK:** A code-driven agent can set its own input schema with
  `evalkit agents create|edit <name> --input-schema schema.json`.
- **Docs:** New pages: Framework mappings (a ready-to-use span mapping for
  ten agent frameworks), Server config (every key and its default), Case
  schema, and CI sync (lint cases on PRs, sync on merge). Docs pages in the
  web UI need no sign-in.

### Improvements

- **CLI:** `evalkit summary <id> --baseline <id>` shows the change in every
  bucket, evaluator and gauge, and lists improved, regressed, new and
  removed cases. Its failing cases include any case where one evaluator
  scored below 0.80.
- **Tracing:** Trajectories built from OTel spans fit more agent shapes:
  subagents only where a tool started them, a user step for every message in
  a chat, and mapping rules `rekind_spans`, `main_agent` and
  `subagent_spans`. `evalkit traces check` and the SDK dry run warn when a
  trajectory looks wrong: no answer span, LLM spans that lost attributes,
  tool spans without tool calls, messages with an unknown role.
- **Evaluators:** Scoring runs at most 16 runs at once; change it with
  `--evaluation-concurrency` on `evalkit run` and `evalkit evaluate`, in the
  SDK, or in the run dialog. Server runs take `runner.evaluation_concurrency`
  from the server config.
- **CLI:** `evalkit lint` errors name the file, the case and the valid keys,
  say where a misplaced evaluator belongs, and cover every broken case in a
  file. `evalkit lint --offline` reports them as findings instead of a
  traceback.
- **Review:** The review queue names the author a case went back to,
  `evalkit sync` says edited cases are back in review, `review request`
  skips cases already waiting on that reviewer, and a promoted case's
  changelog includes its history from the source workspace.
- **CLI:** `evalkit datasets list` and `evalkit review queue` take
  `--workspace`, and `evalkit workspaces list` shows promotion links.
- **CLI:** `evalkit init-server --port` sets the server's port, and
  `evalkit serve` defaults to the port in `server.base_url`.
- **Packaging:** evalkit runs on Python 3.12 as well as 3.13.
- **Server:** A page path returns the web UI only to a browser
  (`Accept: text/html`); other clients get 404.
- **Docs:** The docs site root serves the latest release's pages directly,
  and the Install page covers running the CLI inside a uv project and
  installing the skills.

### Fixes

- **Evaluators:** Fixed scoring an experiment of more than about 40 runs
  failing with 500s: each scoring request uses one database connection, and
  a server out of connections answers 503 with `Retry-After`, which the CLI
  and SDK retry.
- **Evaluators:** Fixed a re-score treating an evaluator that errored after
  an earlier success as done; it retries it.
- **Evaluators:** Fixed an evaluator that raised an exception without a
  message recording an empty error; it records the exception type, such as
  `ReadTimeout`.
- **Tracing:** Fixed Google ADK, Gemini and LangChain-style traces losing
  the model's messages and tool calls (roles such as `model`, `ai` and
  `human`), which made tool evaluators score 0.
- **Tracing:** Fixed an agent behind FastAPI or another instrumented HTTP
  server losing its spans when the server's request span is the trace root,
  once the session id is on the agent's own turn span.
- **CLI:** Fixed `evalkit runs pull` failing the whole batch with a 500 when
  one run's trajectory is missing from the blob store; that run gets a
  `pull_error` and the others are pulled. Every `run.json` has a
  `started_at` field, `null` when the harness gives no creation time.
- **CLI:** Fixed `evalkit lint --agent` failing with an `AssertionError` on a
  code-driven agent.
- **Server:** Fixed a malformed URL in an agent, evaluator, LLM provider or
  trace source answering a server error instead of 400 when
  `allow_private_endpoints` is off.
- **Server:** Fixed an MCP client probing `/.well-known/` on a server
  without SSO getting the web UI's page.

## 0.3.0

_Released 2026-10-30._ evalkit is on PyPI as `evalkit-py`, a server is one
`pip install` away, and API keys live in the database.

### Before you upgrade

- **CLI:** A CLI older than 0.3.0 can't update itself: run
  `uv tool install --reinstall "evalkit-py==<version>"` once, with the
  version `curl <server>/api/cli` prints. A project with a vendored wheel
  moves once: `uv remove evalkit && uv add "evalkit-py[otel]==<version>"`.
- **Server:** Remove `credentials:` and `judge_default:` from the server
  config; a server that still has them refuses to start and prints the
  commands that set the same values. Start it, then run those commands with
  each key in your shell.
- **Server:** Run `evalkit db upgrade` for migration 0013.
- **Server:** Shared agent, evaluator and LLM provider URLs on a private,
  loopback or link-local address are refused. Set
  `allow_private_endpoints: true` when they run in the same cluster or on
  `host.docker.internal`.
- **Server:** Server runs, resumes and re-evaluations judge only with a
  registered LLM provider; another base URL is refused with 400. Register
  one with `evalkit llm-providers create`.
- **Server:** The server no longer serves its own wheel:
  `/api/cli/wheel`, `/api/cli/simple` and `/api/cli/files/...` are gone,
  `GET /api/cli` returns only `version` and `package`, `EVALKIT_WHEEL_DIR`
  is ignored and `evalkit update --vendor` is removed.
- **SDK:** In `evalkit.yaml`, `credentials.judges` is now
  `credentials.llm_providers`.
- **CLI:** `evalkit trace-sources rekey` is now `evalkit db rekey`, and
  re-encrypts every stored key.

### Features

- **Packaging:** evalkit is on PyPI as `evalkit-py` (the command and module
  are still `evalkit`). `evalkit update` installs the server's version and
  moves an `evalkit-py==...` pin in `./pyproject.toml`.
- **Server:** `pip install "evalkit-py[server]"` installs a working server,
  web UI included, and `evalkit init-server` sets one up against a Postgres
  you already run: it creates the database, writes `config.yaml` and `.env`,
  migrates, and registers your LLM as the default judge.
- **Server:** API keys and the default judge are stored in the database, so
  adding an agent, evaluator or LLM provider needs no restart; keys are
  encrypted and never returned. Set keys with `--api-key-env` on
  `evalkit agents` and `evalkit evaluators`, providers with
  `evalkit llm-providers`, and the judge with `evalkit judge-default set`.
- **Server:** The server reports errors to Sentry when the config has a
  `sentry:` block.
- **Docs:** The docs and an SDK API reference are published at
  https://inferedge-ai.github.io/evalkit/, one version per release plus
  `dev`.
- **CLI:** New `evalkit evaluators show` and `evalkit evaluators restore`;
  the web UI can edit an evaluator and restore an archived one.

### Improvements

- **Server:** Writers of a shared workspace can set an agent's harness URL
  and an evaluator's endpoint, not only admins. Changing a URL drops the
  stored key unless the same call sets a new one.
- **Server:** A server run whose agent, evaluator or provider has no key
  calls without one and lists `warnings`, instead of failing with 400.
- **Server:** `evalkit serve` and `evalkit db upgrade` read a `.env` beside
  `config.yaml`; variables already in the environment win.

## 0.2.0

_Released 2026-10-27._ Review no longer decides which cases run, and
experiment lists show only the scores they have.

### Before you upgrade

- **CLI:** Run `evalkit update --server <url>` once the server is on 0.2.0;
  a 0.1 CLI refuses every other command against it.
- **CLI:** `--include-drafts` and `include_drafts=` are removed; drop them
  from scripts and CI, or the command fails.
- **Review:** The Draft review state is now Unreviewed: `unreviewed` in the
  API and `evalkit datasets list --state`, and the sync report's
  `review.now_draft` is `review.now_unreviewed`.

### Improvements

- **Review:** `evalkit run`, `run_experiment` and the run dialog run every
  selected case of a shared dataset, whatever its review state; review
  decides what gets promoted.
- **CLI:** `evalkit lint <dir>` checks fixture keys like `--offline` does,
  so CI can drop its separate offline lint.
- **Web UI:** Experiment lists show a score column only for buckets some
  experiment was scored on, plus `eff` and `custom` when present.
- **Web UI:** An experiment shows its judge model and reasoning effort, and
  experiment lists have an optional `judge` column.
- **Web UI:** The Agents page lists archived agents under Archived (N), so
  you can open one and restore it.
- **Web UI:** An empty workspace you can't write to says it's read-only
  instead of suggesting `evalkit run` or `evalkit sync`.

### Fixes

- **Web UI:** Fixed Reviews and Runs keeping an old error after a later
  load succeeds.
- **CLI:** Fixed `evalkit traces check` on an unknown agent not saying how
  to create it.
- **Sign-in:** Fixed OAuth clients such as Claude failing to connect to
  `/mcp` with `mcp.oauth` set, because the protected-resource metadata added
  a trailing slash to the issuer.

## 0.1.0

_Released 2026-10-26._ The first public release of evalkit, a self-hosted
system for improving AI agents: mine failures from real runs into eval
cases, have domain experts review them, run them against your agent, and fix
what fails. See the [README](README.md) to self-host it or to connect to an
existing server.
