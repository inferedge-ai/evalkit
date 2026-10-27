# Where runs happen

Every dataset and agent lives in a workspace:

- **Personal workspace:** one per user, named after the username. Only the owner writes to it.
- **Shared workspace:** created by an admin, e.g. `dev` and `prod`. Members with the writer role write to it; reviewers approve its cases.
- **Who can read:** every logged-in user can read every workspace, including other users' personal workspaces: datasets, cases, experiments, runs, trajectories, spans and sessions, and fixtures. Only the owner writes to a personal workspace; admins and writers write to a shared one. The web UI marks someone else's personal workspace "read-only".
- **Traces are visible too:** traces you import or export into your personal workspace, such as production traces pulled for case mining, can be read by everyone on the server. No workspace setting keeps them private. If traces hold data some users must not see, scrub it in your agent before export, or run a separate evalkit server for those traces. A mapping's `exclude_spans` does not help here: the server stores spans as received and only skips them when it builds a trajectory.
- **Promotion:** an admin can link two shared workspaces (`evalkit workspaces promote-to dev prod`). Approving a case in `finance@dev` then adds that version to `finance@prod`, approved, with its overlays. `prod` is fed only by promotion: sync and review decisions there are refused, comments allowed. Create each target dataset once with `evalkit datasets create finance@prod`. One target per source, one source per target.

The agent you pick decides who drives the run. The dataset and the agent together decide where the experiment is stored:

- **Both shared:** the experiment lives in the dataset's workspace.
- **Otherwise:** it lives in your personal workspace.

Example with workspaces `@alice` (personal), `@dev` and `@prod` (shared).
`myagent` is alice's personal agent; `ouragent` is the team's deployed agent,
with one entry per shared workspace.

| #   | Who / entry                                                     | Dataset                      | Agent                             | Driver         | Agent runs on      | Trajectory from                       | Experiment in | Use case                                            |
| --- | --------------------------------------------------------------- | ---------------------------- | --------------------------------- | -------------- | ------------------ | ------------------------------------- | ------------- | --------------------------------------------------- |
| 1   | alice, SDK `run_experiment(dataset="finance", agent="myagent")` | finance@alice                | myagent@alice (code-driven)       | alice's Python | laptop, in-process | OTel → collector → server builds ATIF | @alice        | EDD inner loop (default)                            |
| 2   | alice, `evalkit run finance --agent myagent`                    | finance@alice                | myagent@alice (localhost harness) | alice's CLI    | laptop             | harness ATIF → CLI → server           | @alice        | a Harness API agent on this machine                 |
| 3   | alice, `evalkit run finance@prod --agent myagent`               | finance@prod (approved only) | myagent@alice                     | alice's CLI    | laptop             | harness ATIF → CLI → server           | @alice        | does a local change break the released set          |
| 4   | alice, `evalkit run finance --agent ouragent@dev`               | finance@alice                | ouragent@dev                      | server         | dev cluster        | harness ATIF or OTel                  | @alice        | try new cases on the deployed dev agent             |
| 5   | anyone, UI or `run finance@dev --agent ouragent@dev`            | finance@dev                  | ouragent@dev                      | server         | dev cluster        | harness ATIF or OTel                  | @dev          | regression after a deploy or merge                  |
| 6   | anyone, `run finance@dev --agent ouragent@prod`                 | finance@dev                  | ouragent@prod                     | server         | prod cluster       | harness ATIF or OTel                  | @dev          | does prod pass cases approved in dev                |
| 7   | anyone, UI or `run finance@prod --agent ouragent@prod`          | finance@prod                 | ouragent@prod                     | server         | prod cluster       | harness ATIF or OTel                  | @prod         | release gate                                        |
| 8   | deployed ouragent with an agent token                           | none                         | ouragent@prod                     | none           | prod cluster       | OTel → collector, browsed in Runs     | none          | trajectory debugging of deployed traffic (optional) |
| ✗   | anyone, server or UI, `--agent myagent@alice`                   | any                          | myagent@alice                     | refused        |                    |                                       |               | the server cannot reach a laptop                    |
| ✗   | bob, `--agent myagent@alice`                                    | any                          | myagent@alice                     | refused        |                    |                                       |               | only the owner runs a personal agent                |

## Several branches

- `evalkit run` syncs the dataset directory first. The dataset in your
  workspace then matches whichever branch ran last, and the dataset page shows
  that sync's branch and sha.
- An experiment pins its case versions when it starts. A sync from another
  branch never changes a running or finished experiment. Cases removed by that
  sync are soft-removed, and pinned versions remain.
- Fixture bytes are content-addressed, so two branches never overwrite each
  other's bytes under the same key.
- `--resume` does not sync, and runs the pinned versions, so it is safe from any
  branch.
- Agent config is one row per agent, not per branch.
