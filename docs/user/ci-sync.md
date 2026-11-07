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
- **On merge:** `evalkit sync <suite-dir> --workspace dev`
  writes the suite into `<suite>@dev`. The dataset name is the directory name.

## What you need

- The server URL, as a CI variable `EVALKIT_SERVER_URL` (e.g. `{{origin}}`).
- Credentials for a machine user. With single sign-on: a machine client at
  the identity provider (see [Single sign-on](sso.md)), its id and secret as
  CI secrets `EVALKIT_OIDC_CLIENT_ID` and `EVALKIT_OIDC_CLIENT_SECRET`, and
  an admin runs `evalkit users add ci-bot --subject <sub>` once and grants it
  writer on the workspace: `evalkit workspaces members add dev ci-bot --role
  writer`. Without single sign-on: a dedicated user with a password, created
  with `evalkit users add ci-bot` and granted writer the same way, with the
  password as a CI secret. Never the admin account.
- A shared workspace to sync into, e.g. `dev` (see [Workspaces](workspaces.md)).

Each job does three things:

1. Installs the CLI at the server's version, so CI always runs the server's
   build.
2. Authenticates as the machine user. Every server call needs a login, lint included.
3. Runs `evalkit lint` or `evalkit sync`.

`--actor` is admin only. A machine user records itself as the actor, and
`--actor` needs an admin machine user, which `users promote` refuses, so the
case changelog shows `ci-bot` for a sync from CI. Where an admin does pass
`--actor`, it is a free-text label: the server stores it as given and does
not look it up among evalkit users.

Pass values from the commit through `env:`. Never write `${{ ... }}` straight into a `run:` script:
whoever pushes the commit controls those values, and the job holds the
machine user's credentials.

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
    env:
      EVALKIT_OIDC_CLIENT_ID: ${{ secrets.EVALKIT_OIDC_CLIENT_ID }}
      EVALKIT_OIDC_CLIENT_SECRET: ${{ secrets.EVALKIT_OIDC_CLIENT_SECRET }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - name: Install the evalkit CLI
        run: uv tool install --reinstall "evalkit-py==$(curl -fsSL "$EVALKIT_SERVER_URL/api/cli" | jq -r .version)"
      - name: Lint
        if: github.event_name == 'pull_request'
        run: |
          for suite in eval-cases/*/; do
            evalkit lint "$suite" --dataset "$(basename "$suite")@dev"
          done
      - name: Sync
        if: github.event_name == 'push'
        run: |
          for suite in eval-cases/*/; do
            evalkit sync "$suite" --workspace dev
          done
```

Without single sign-on, keep a `Log in` step: `evalkit login --url
"$EVALKIT_SERVER_URL" --username ci-bot --password "$EVALKIT_CI_PASSWORD"`.

The same steps work in any CI: install, authenticate, lint or sync.

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
