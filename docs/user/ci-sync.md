# Sync cases from CI

Keep eval cases in your agent's repo and let CI put them on the server. A PR
lints the cases; a merge to your default branch syncs them into a shared
workspace, where reviewers approve them.

This page uses the dev and prod setup described in
[Example setup: personal → dev → prod](workspaces.md#example-setup-personal-dev-prod):
developers try cases in their personal workspaces, CI syncs merged cases into
`@dev`, and approvals in `@dev` promote them to `@prod`. If your workspaces
differ, change `dev` below to the workspace CI should sync into.

- **On every PR:** `evalkit lint <suite-dir> --dataset <suite>@dev` fails the
  job on errors.
- **On merge:** `evalkit sync <suite-dir> --workspace dev --actor <author>`
  writes the suite into `<suite>@dev`. The dataset name is the directory name.

## What you need

- The server URL, as a CI variable `EVALKIT_SERVER_URL` (e.g. `{{origin}}`).
- The `admin` password, as a CI secret `EVALKIT_ADMIN_PASSWORD`. It is the
  value the server was first started with. Store the password, not a token:
  tokens expire, the password does not.
- A shared workspace to sync into, e.g. `dev` (see [Workspaces](workspaces.md)).

Each job does three things:

1. Installs the CLI at the server's version, so CI always runs the server's
   build.
2. Logs in as `admin`. Every server call needs a login, lint included.
3. Runs `evalkit lint` or `evalkit sync`.

`--actor` is admin only. It records the merge author in the case changelog,
instead of `admin`. It is a free-text label: the server stores it as given
and does not look it up among evalkit users. The example passes the commit
author's email; pass an evalkit username instead if your CI can map one.

Pass values from the commit, such as the author's email, through `env:`, as
the example does. Never write `${{ ... }}` straight into a `run:` script:
whoever pushes the commit controls those values, and the job holds the admin
password.

## GitHub Actions example

The cases live in `eval-cases/<suite>/`. Change the paths and the workspace
to match your repo.

```yaml
name: eval cases
on:
  pull_request:
    paths: ["eval-cases/**"]
  push:
    branches: [main]
    paths: ["eval-cases/**"]

env:
  EVALKIT_SERVER_URL: ${{ vars.EVALKIT_SERVER_URL }}

jobs:
  evalkit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - name: Install the evalkit CLI
        run: uv tool install --reinstall "evalkit-py==$(curl -fsSL "$EVALKIT_SERVER_URL/api/cli" | jq -r .version)"
      - name: Log in
        run: evalkit login --url "$EVALKIT_SERVER_URL" --username admin --password "$EVALKIT_ADMIN_PASSWORD"
        env:
          EVALKIT_ADMIN_PASSWORD: ${{ secrets.EVALKIT_ADMIN_PASSWORD }}
      - name: Lint
        if: github.event_name == 'pull_request'
        run: |
          for suite in eval-cases/*/; do
            evalkit lint "$suite" --dataset "$(basename "$suite")@dev"
          done
      - name: Sync
        if: github.event_name == 'push'
        env:
          AUTHOR: ${{ github.event.head_commit.author.email }}
        run: |
          for suite in eval-cases/*/; do
            evalkit sync "$suite" --workspace dev --actor "$AUTHOR"
          done
```

The same steps work in any CI: install, log in, lint or sync.

## Notes

- Sync reads the git branch and sha itself. Pass `--version` and `--branch`
  when the checkout has no git history.
- Sync uploads any fixture bytes the server does not hold yet. Set
  `fixtures:` in `evalkit.yaml` if your cases use fixtures (see
  [Config](config.md)).
- A full suite sync removes cases that are no longer on disk. Experiments
  that already ran keep the case versions they used.
- `--dataset` on lint lets the server check case ids and overlay targets
  against the other datasets. Without it, those checks only run at sync.
- If `dev` is linked to `prod` for promotion, sync only into `dev`. `prod`
  refuses sync; approved cases reach it through promotion.
- Synced cases start Unreviewed. Reviewers of the workspace approve them; see
  the [Quickstart](quickstart.md).
