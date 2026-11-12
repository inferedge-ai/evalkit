# Contributing to evalkit

Thanks for your interest in evalkit. Issues, questions and pull requests are
all welcome.

## Issues and questions

- **Bugs and feature requests:** open an
  [issue](https://github.com/inferedge-ai/evalkit/issues/new/choose). A clear
  report with steps to reproduce helps most.
- **Questions and ideas:** ask in
  [Discussions](https://github.com/inferedge-ai/evalkit/discussions).
- **Security vulnerabilities:** report them privately through
  [GitHub](https://github.com/inferedge-ai/evalkit/security/advisories/new),
  or email [tech@inferedge.com](mailto:tech@inferedge.com). Don't post them in
  a public issue.

## Pull requests

- For anything bigger than a small fix, open an issue first so we can agree
  on the approach before you write the code.
- Branch from `develop` and open your pull request against `develop`.
  `main` only receives releases.
- Keep each pull request to one change.
- If your change affects behaviour, an interface, a dependency, the schema,
  packaging or the build, add an entry under `## Unreleased` in
  `CHANGELOG.md` saying what changed and, when it is not obvious, why.
- Don't bump the version in `pyproject.toml`; releases do that.
- If your change touches the UI, run it locally, click through what changed,
  and add screenshots to the pull request description.
- Design specs and implementation plans are not checked in. If you wrote
  one, share it in the pull request: paste it into the description or link
  to it.
- CI must pass before a pull request can merge.

## Development setup

You need [uv](https://docs.astral.sh/uv/), Docker (the tests start
Postgres in a container) and Node.js 24 for the UI.

```bash
uv sync --extra server
uv run pytest              # tests
uv run ruff check          # lint
uv run ruff format         # format
uv run mypy src/evalkit    # types
cd ui && npm ci && npm test
```

To run against your own Postgres instead of a container, set
`EVALKIT_TEST_PG_URL`. To run the server and UI locally, see "Local server"
in [AGENTS.md](AGENTS.md).

[AGENTS.md](AGENTS.md) also holds the project's terms, code layout,
layering rules and code style. Please follow them.

## Writing the user docs

The pages in `docs/user/` render in the web UI's docs page, on the docs site
(MkDocs) and as plain markdown on GitHub. Three markers work in both
renderers:

- **Tabs.** Put `<!-- tabs -->` and `<!-- /tabs -->` on their own lines around
  the block. Inside it, each `#### Label` heading starts a tab, and its body
  runs to the next `####` or the closing marker. The first tab is selected.
  Outside the markers, `####` is an ordinary heading.
- **Row anchors.** Put `<!-- anchor-rows -->` on the line right before a table.
  Each body row gets an id from its first cell: lowercase, every run of
  characters that is not a letter or digit becomes one `-`, and leading and
  trailing `-` are removed. `App run` becomes `app-run`, `` `evalkit.yaml` ``
  becomes `evalkit-yaml`. Link to a row as `concepts.md#app-run`. A repeated
  or empty id fails the docs build.
- **Heading links.** Headings get the ids MkDocs makes: `## The CLI, as a
  global tool` is `#the-cli-as-a-global-tool`. Link as `install.md#...`, or
  `#...` on the same page. Tab labels have no id.
- **Admonitions.** Write `!!! note` or `!!! note "Custom title"`, then the
  body indented by four spaces. The body can hold paragraphs, lists, links
  and fenced code. Types: `note`, `info`, `tip`, `warning` and `danger`,
  plus Material's other names (`abstract`, `summary`, `tldr`, `todo`,
  `question`, `help`, `faq`, `example`, `quote`, `cite` look like `info`;
  `hint`, `important`, `success`, `check`, `done` like `tip`; `caution`,
  `attention` like `warning`; `error`, `failure`, `fail`, `missing`, `bug`
  like `danger`). Any other type looks like `note`. Without a title the box
  is titled after its type; `""` gives no title.
- **Images.** Put them in `docs/user/images/` and write
  `![alt text](images/<file>.png)`. `evalkit skills install` leaves the folder
  out of the copy it makes for coding agents.

## Code of conduct

Everyone taking part follows our [Code of Conduct](CODE_OF_CONDUCT.md).

Contributions are made under the repository's [Apache-2.0 License](LICENSE). There is no Contributor License Agreement.
