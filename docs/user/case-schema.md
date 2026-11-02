# Case schema

This page lists every key a case YAML file may hold, with one example for each evaluator. `evalkit lint` checks a file against this schema. The machine-readable form is `cases-v1.json`, written by `evalkit contracts export`.

## File shapes

A case file is one of:

- a single case (a mapping with `id`)
- a list of cases and overlays (an overlay is an entry with `extends`)
- a mapping with any of `defaults`, `cases` and `overlays`

```yaml
defaults:
  tags: [balance-sheet]
  input: {company: acme}
  max_response_time: 90
cases:
  - id: acme-total-assets-fy2024
    input:
      question: What were Acme's total assets as of 31 March 2024?
    evaluators:
      output:
        answer_rubrics:
          rubrics:
            - rubric: Total assets as of 31 March 2024 are $4.2 billion.
              type: factual_retrieval
```

`defaults` takes only `input`, `tags`, `max_response_time` and `agents` (for overlays). A case's own `input` keys and `max_response_time` win over the defaults; tags are merged.

## Case keys

| Key | Type | Meaning |
| -- | -- | -- |
| `id` | string, required | The case's identity, shared by every workspace. |
| `tags` | list of strings | Labels for `evalkit run --tag`. The case's folder in the suite is added as a tag too. |
| `input` | mapping | Sent to the agent as is. Most agents read `input.question`. |
| `fixtures` | list of strings | Keys from `fixtures.yaml` to attach as files. |
| `expected` | mapping | Free-form ground truth, passed to custom evaluators. |
| `evaluators` | mapping | Which evaluators score this case. See below. |

## Evaluators

Evaluators sit under `evaluators.output`, `evaluators.trajectory` or `evaluators.custom`. The scorecard groups their scores into buckets, but the bucket names are not YAML keys:

| Scorecard bucket | YAML key |
| -- | -- |
| content | `evaluators.output.answer_rubrics` |
| presentation | `evaluators.output.presentation_judge` |
| efficiency | `evaluators.output.max_response_time` |
| trajectory | `evaluators.trajectory.trajectory_rubrics`, `evaluators.trajectory.tool_trajectory_match`, `evaluators.trajectory.required_tools_called` |
| custom | `evaluators.custom.<name>` |

### answer_rubrics

What a correct answer contains. The judge marks each rubric `satisfied`, `incorrect` or `absent`.

```yaml
evaluators:
  output:
    answer_rubrics:
      rubrics:
        - rubric: Revenue for FY2024 is $3.52 billion.
          type: factual_retrieval        # or derived_computation, analysis_interpretation
          source: the FY2024 annual report  # optional, factual_retrieval only
        - rubric: Revenue grew 12% from FY2023 to FY2024.
          type: derived_computation
```

Write one assertion per rubric. A rubric that bundles two ("states $300/month and explains why") fails as a whole when the answer gets only one part right, so a correct answer can be marked wrong. Split it into two rubrics.

### presentation_judge

How the answer shows its content: units, columns, an artifact relayed rather than restated. Each entry has exactly one of `rubric` (a property the answer must have) or `forbid` (a property it must not have). At least one entry must be a `rubric`.

```yaml
evaluators:
  output:
    presentation_judge:
      rubrics:
        - rubric: A store count for each fiscal year FY2021–FY2025, as a table or a labelled list.
          points: 2      # optional weight, 1–10, default 1
        - forbid: Figures given without a currency unit.
          note: Rupee or dollar both fine.   # optional, shown to the judge
```

### max_response_time

A cap on the run's wall-clock time, in seconds. The value is a mapping, not a bare number. Only `defaults.max_response_time` also takes a bare number.

```yaml
evaluators:
  output:
    max_response_time:
      max_seconds: 90
```

### trajectory_rubrics

Checkpoints the trajectory must show, one sentence each. Order is not graded.

```yaml
evaluators:
  trajectory:
    trajectory_rubrics:
      rubrics:
        - Retrieves FY2024 Platform revenue as $3,522,776,000.
        - Rounds the final figure to the nearest basis point.
```

### tool_trajectory_match

Expected tool calls, matched by name and args. Tool names must match the trajectory exactly. The score is the share of expected calls found; `ordered: true` also requires them in this order.

```yaml
evaluators:
  trajectory:
    tool_trajectory_match:
      ordered: false
      expected:
        - name: get_financials
          args: {ticker: ACME, period: FY2024}
          args_mode: subset    # subset, exact, ignore or substring
          match_overrides:     # optional, per arg: equals or substring
            period: substring
```

### required_tools_called

Tools the agent must call at least once. The score is the share called.

```yaml
evaluators:
  trajectory:
    required_tools_called:
      tools: [get_financials, search_filings]
```

### custom

Remote evaluators and Python evaluators, keyed by their name. The block is passed to the evaluator as `config`, untouched.

```yaml
evaluators:
  custom:
    sql_matches:
      table: revenue
```

## Python evaluators

A Python evaluator receives an `EvalCase` and an `EvalRun` and returns a float, a `ScoreItem` or a list of them. All three import from `evalkit.sdk`.

```python
from evalkit.sdk import EvalCase, EvalRun, ScoreItem, evaluator


@evaluator(name="sql_matches")
def sql_matches(case: EvalCase, run: EvalRun) -> ScoreItem:
    table = case.config["table"]
    return ScoreItem(name="sql_matches", score=float(table in run.answer))
```

| `EvalCase` field | Meaning |
| -- | -- |
| `id` | Case id. |
| `input` | Case input, verbatim. |
| `expected` | The case's `expected` block, or `None`. |
| `config` | This evaluator's block from `evaluators.custom`. |

| `EvalRun` field | Meaning |
| -- | -- |
| `answer` | The final answer. A structured output as JSON text when the agent returned only that. |
| `output` | The structured output, or `None`. |
| `trajectory` | The ATIF trajectory as a dict. |

`ScoreItem` has `name`, `score` (`None` means no reading), `label` and `explanation`.

## Overlays

An overlay adds evaluators to a case in another suite, for the agents it names. It may add `tags`, `evaluators.custom` and the `tool_trajectory_match` and `required_tools_called` trajectory evaluators.

```yaml
overlays:
  - extends: acme-total-assets-fy2024
    agents: [support-bot]
    evaluators:
      trajectory:
        required_tools_called:
          tools: [get_financials]
```
