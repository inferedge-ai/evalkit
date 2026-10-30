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
- If your change affects what users can do or see (a command, flag,
  endpoint, field, UI control), add an entry under `## Unreleased` in
  `CHANGELOG.md`.
- Don't bump the version in `pyproject.toml`; releases do that.
- If your change touches the UI, run it locally, click through what changed,
  and add screenshots to the pull request description.
- CI must pass before a pull request can merge.

## Development setup

You need [uv](https://docs.astral.sh/uv/), Docker (the tests start
Postgres in a container) and Node.js 24 for the UI.

```bash
uv sync --extra server --extra otel
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

## Code of conduct

Everyone taking part follows our [Code of Conduct](CODE_OF_CONDUCT.md).
