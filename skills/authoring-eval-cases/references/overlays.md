# Whitebox enrichment via overlays

An on-demand flow — run it when the developer asks to enrich a case with
harness-specific assertions. Enrichment ADDS whitebox checks to a blackbox
case as an **overlay**: a YAML entry in the client's own cases directory
(`extends: <case-id>`) that extends a case in another suite by id and
attaches whitebox checks: evalkit's built-in tool checks under
`evaluators.trajectory`, or config for the client's Custom Evaluator API
endpoints under `evaluators.custom`.

```yaml
overlays:
  - extends: <case-id>
    agents: [<agent name>]
    tags: [<additive tags, optional>]
    evaluators:
      trajectory:
        required_tools_called: { tools: [<real tool names>] }
        tool_trajectory_match: { expected: [ ... ] }
      custom:
        <registered evaluator>: { ... }
```

Set `agents` once for a whole file with `defaults:`:

```yaml
defaults:
  agents: [<agent name>]
overlays:
  - extends: <case-id>
    evaluators: { ... }
```

Rules:

- `agents` is required. The overlay scores only runs of the agents it names;
  every other agent runs the case without it. Agents match by name in any
  workspace: `agents: [support-bot]` covers both `support-bot` and `support-bot@<you>`.
- Several overlay datasets may extend the same case. Two of them may not set
  the same evaluator for the same agent on one case: the second sync fails
  with `E-OVERLAY-CLASH`.
- An overlay may only carry `extends`, `agents`, additive `tags`,
  `evaluators.trajectory` (only `tool_trajectory_match` and
  `required_tools_called`) and `evaluators.custom`. Touching `input`,
  `expected`, or any other built-in evaluator config is a lint error — ground
  truth is not overlay-editable. Stripping the overlay must leave a portable
  case.
- Every custom evaluator name must be registered in the deployment's
  config.yaml (`custom_evaluators:`), and the client must host the matching
  Custom Evaluator API endpoint that knows how to score that config against
  the run's answer + ATIF trajectory. The built-in tool checks need no
  endpoint; see [`tool-checks.md`](tool-checks.md) for their config.
- Overlays live in the client's YAML dir and reach the server via
  `evalkit sync` — removing one from the dir strips the enrichment on the next
  directory sync.

## Run-then-pin

1. Run the case single-case (`evalkit run <dataset> --agent <agent>
   --case-id <id>`; the run syncs first); collect the experiment.
2. Read the observed trajectory from the collected bundle. Derive the
   enrichment from what actually happened:
   - required tools: the real data tools the run used;
   - trajectory match: observed calls with their args (prefer `substring`
     for free-text args);
   - call caps: observed counts plus slack (a cap of observed+1 fails the case
     on the first regression into retry-grinding without flaking on normal
     variance).
3. Cross-check the observed tool choices against the harness's canonical tool
   surface (its tool definitions / MCP server source). If a better tool or
   trajectory exists than the one observed, pin the better one and let the
   case go red: the case is the contract, and the red is a finding for the
   EDD loop.
4. Curate — pin intent, not accident:
   - Date literals in args are correct when the case input is date-anchored
     (they assert the agent honoured the as-of). Do not pin values the agent
     derived from run time.
   - Do not pin incidental arg values (page sizes, orderings) that a
     different-but-correct run would legitimately vary.
5. `evalkit lint <file> --dataset <slug>` the overlay file (unregistered
   evaluator names and bad targets are errors; the target check needs
   `--dataset`), then re-run the case to confirm the enriched assertions
   hold.
