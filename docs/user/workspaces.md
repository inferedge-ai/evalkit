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

## Example setup: personal → dev → prod

A common setup with two shared workspaces, one per deployed environment of
your agent:

- **Personal workspaces:** each developer writes and runs cases in their own
  workspace, on their laptop.
- **`@dev`:** a PR adds or changes cases in the repo. On merge, CI syncs them
  into `@dev`. Reviewers of `@dev` approve them there.
- **`@prod`:** the promotion target of `@dev`. Approving a case in `@dev`
  copies that version into `@prod`, already approved. Nothing else writes
  cases to `@prod`, so it always holds the released set.

```text
@alice ──PR merged, CI syncs──▶ @dev (Unreviewed) ──reviewer approves──▶ @dev (Approved) ──promotion──▶ @prod (Approved)
```

### Set it up

An admin runs these once:

```bash
evalkit users add alice                                 # prompts for a password; once per user
evalkit users add bob
evalkit workspaces create dev
evalkit workspaces create prod
evalkit workspaces promote-to dev prod                  # approvals in @dev land in @prod
evalkit workspaces members add dev alice --role writer  # may sync and run in @dev
evalkit workspaces members add dev bob --role reviewer  # may approve cases in @dev
evalkit datasets create finance@prod                    # once per dataset, before its first approval
```

Then:

1. Set up CI to lint cases on every PR and sync them into `@dev` on merge.
   See [CI sync](ci-sync.md).
2. If the server drives your deployed agent, register it once per
   workspace, pointing each at that environment's deployment:
   `evalkit agents create ouragent --workspace dev --harness-url …` and the
   same with `--workspace prod`.

### Day to day

1. **Write and try a case.** Alice adds `finance/net-debt.yaml` and runs it
   against her own agent: `evalkit run finance --agent myagent`. This syncs
   the directory into `finance@alice` and runs it there.
2. **Open a PR.** CI runs `evalkit lint` on the cases and fails on errors.
3. **Merge.** CI runs `evalkit sync` into `finance@dev`. The new case is
   Unreviewed.
4. **Review.** Alice asks for a review:
   `evalkit review request finance@dev --case-id net-debt --reviewer bob`.
   Bob approves it: `evalkit review approve finance@dev net-debt`.
   He can approve several at once (`evalkit review approve finance@dev
   net-debt fx-rate`) or every case waiting on him
   (`evalkit review approve finance@dev --all-waiting`). To ask a question
   first: `evalkit review comment finance@dev net-debt --comment "which
   fiscal year?"`; resolve it later by its id: `evalkit review resolve 12`.
   `evalkit review queue finance@dev` lists what is waiting in one dataset.
5. **Promotion.** The approval adds that version of the case to
   `finance@prod`, approved.
6. **Run.** `finance@dev` holds every merged case, for regression runs
   after a merge. `finance@prod` holds the approved ones, for the release
   gate (rows 5 and 7 in the table above).

Changing a case starts the loop again. The new version is Unreviewed in
`@dev`, and `@prod` keeps the old approved version until the new one is
approved.

A change to a case that is waiting for review, or was sent back with
changes requested, goes back to the same reviewer on sync: you don't need
to run `review request` again. If you do, cases already waiting on that
reviewer are left as they are.

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
