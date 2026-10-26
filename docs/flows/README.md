# User flows

Black-box descriptions of what evalkit users do and see. The evalkit server is
one box: no store, database or blob details.

Each doc has: actors, mermaid sequence diagrams, expected outcomes (the checks a
tester runs) and error cases.

| # | Flow | Actors |
| -- | -- | -- |
| 01 | [Install, log in](01-install-login.md) | Developer |
| 02 | [User admin](02-user-admin.md) | Admin |
| 03 | [Lint and sync a suite](03-lint-and-sync-suite.md) | Developer, CI |
| 04 | [Fixtures](04-fixtures.md) | Developer, CI, viewer |
| 05 | [Case review](05-case-review.md) | Developer, reviewer |
| 06 | [Server run](06-server-run.md) | Any user, agent harness |
| 07 | [Local harness run](07-local-harness-run.md) | Developer, local harness |
| 08 | [SDK run with OTel traces](08-sdk-run-with-otel.md) | Developer's script |
| 09 | [Browse an experiment, scorecard and pull runs](09-browse-experiment-scorecard-collect.md) | Viewer, developer |
| 10 | [Compare experiments](10-compare-experiments.md) | Viewer, developer |
| 12 | [Agent tokens, the collector, and the Runs browser](12-agent-tokens-collector-runs-browser.md) | Developer, deployed agent |
| 13 | [Trace mappings and checks](13-trace-mappings-and-checks.md) | Developer |
| 14 | [Import sessions from a trace source](14-import-from-trace-source.md) | Developer |
| 15 | [Harness check](15-harness-check.md) | Developer, any user |
| 16 | [Workspace and member admin](16-workspace-and-member-admin.md) | Admin |
| 17 | [Agent, evaluator and trace source admin](17-agent-and-evaluator-admin.md) | Admin, workspace writer, developer |
| 18 | [Try-it stack](18-try-it-stack.md) | Developer |
