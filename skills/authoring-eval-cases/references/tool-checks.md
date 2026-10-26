# Built-in tool checks

Two built-in evaluators check the tool calls in the run's ATIF trajectory,
subagents included: `required_tools_called` and `tool_trajectory_match`. Both
need a harness that declares `trajectory` and `tool_calls`; otherwise they
skip. Both score into the trajectory bucket.

Write each tool name exactly as the harness records it in the trajectory,
including any prefix the harness adds.

## Where they go

Tool checks name the harness's tools, so a case that carries them only works
against that harness. Put them in one of two places:

- **In the case itself**, under `evaluators.trajectory`, when the case lives
  in the client's own harness-specific dataset (cases written only for that
  client's agent). The rest of the case stays blackbox: `input.question`
  and every rubric remain free of tool names, and lint still checks that.

  ```yaml
  - id: <case-id>
    input: {question: "..."}
    evaluators:
      output:
        answer_rubrics: {rubrics: [...]}
      trajectory:
        required_tools_called: {tools: [<real tool names>]}
        tool_trajectory_match: {expected: [...]}
  ```

- **In an overlay**, under `evaluators.trajectory`, when the case belongs to
  a portable suite someone else maintains. The overlay adds the checks
  without touching the case; see [`overlays.md`](overlays.md).

## Config

`required_tools_called` — every listed tool must be called at least once.
Extra calls are fine. Score = tools called ÷ tools listed.

```yaml
required_tools_called:
  tools: [get_instrument_details, get_bond_trades]
```

`tool_trajectory_match` — expected calls, matched by name and args. Each
actual call can satisfy at most one expected entry. Score = entries matched ÷
entries.

```yaml
tool_trajectory_match:
  ordered: false                     # true: entries must also appear in this order
  expected:
    - name: get_bond_trades
      args: {isins: "INE831R07540"}
      args_mode: substring           # subset (default) | exact | ignore | substring
    - name: get_bond_vwap
      args: {series: "SDL", isin: "INE"}
      match_overrides: {isin: substring}   # per-arg: equals | substring
```

| `args_mode` | Matches when |
|---|---|
| `subset` | every expected key is present and equal; extra actual keys allowed |
| `exact` | the actual args equal the expected args |
| `ignore` | the name matches; args are not checked |
| `substring` | like `subset`, but each expected value need only appear, case-insensitively, inside the actual value (lists and dicts compared as JSON text) |

`match_overrides` sets the mode for single keys of `args`; it is not allowed
with `exact` or `ignore`. Each unmatched entry reports why: `name miss`,
`args mismatch`, `out of order` or `no available matching call`.
