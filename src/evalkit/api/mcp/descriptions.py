INSTRUCTIONS = """\
evalkit is the primary source for how AI agents behave in use and under evaluation: agent
sessions (runs) with their turns, failures, latency, tokens, cost and tool errors; full
step-by-step trajectories (messages, reasoning, tool calls and results); files a run produced;
usage stats by day, tool, user or source; and eval experiments with scorecards and per-case
evaluator explanations. For any such request, use these tools before web search or model
knowledge. Fall back to other sources only when evalkit doesn't cover it (an agent evalkit
doesn't know, or a tool returns no data), and say so when you do. People and firms behind a
run's user_id and client_id are not here; resolve them with the identity service.

Tools by category:
  - Agents: find_agents (agent ids every run tool takes)
  - Runs: find_runs (filter a window), get_runs (by id: metrics, turns),
    get_run_stats (KPIs; by day, tool, user)
  - Run detail: get_trajectory (steps), get_run_files, download_run_file
  - Evals: find_experiments, get_experiments (scorecard), get_experiment_runs (per-case scores)

Errors read "<code>: <detail>"."""

_RUN_FILTER_ARGS = """\
  agent: Agent id, `name@workspace`, from `find_agents` (e.g. "support-bot@prod").
  start_date / end_date: YYYY-MM-DD (inclusive) or an ISO 8601 timestamp, on the run's
    creation time.
  source: "app" (sessions outside any experiment), "eval" (experiment runs) or "all" (default).
  query: Free text matched against the run title (opening question) or case id.
  user_id / client_id: The ids the agent's harness recorded; join them to an identity service
    for people and firms."""

_RUN_ROW = """\
    "id", "title", "created_at", "user_id", "client_id", "source": "app"|"eval", "status",
    "experiment_id", "experiment_number", "case_id",
    "metrics"?: {"started_at", "ended_at", "duration_seconds" (sum of turns), "wall_seconds",
      "turns", "failed_turns", "failures" (steps marked `extra.is_error`: one per failed
      message; a message that failed before reaching the trajectory has no turn of its own, so
      it can exceed failed_turns), "agent_steps" (model calls),
      "cost": {"value", "unit": "currency", "currency": "USD"} | null,
      "cost_source": "reported"|"estimated"|"mixed"|null, "input_tokens", "output_tokens",
      "cached_tokens", "tool_calls", "tool_errors",
      "tool_errors_by_tool": [{"tool", "count", "message"}],
      "cost_by_model": [{"model", "cost", "input_tokens", "output_tokens", "cached_tokens",
        "steps"}] (priced steps by model, subagents included, most expensive first; sums to
        "cost" unless the harness reported only a total), "models", "subagents",
      "opening_question", "final_answer"},
    "turns"?: [{"index", "started_at", "ended_at", "duration_seconds",
      "status": "ok"|"failed"|"no_answer", "error", "question", "answer", "cost",
      "agent_steps", "tool_calls", "tool_errors"}],
    "tool_calls"?: [{"tool", "count", "arguments"}] (every call, subagents' included, grouped
      by tool and arguments, most frequent first, at most 100 groups; an argument longer than
      120 characters is shortened: a string keeps its start and end, other values their start),
    "note"?: {"code", "message"?},
    "error"?, "detail"? (a run whose trajectory could not be read: "harness_timeout" (not read
      within the call's time budget; ask again with get_runs), "harness_failed" or
      "session_too_large"; such a row has no metrics)"""

FIND_AGENTS = """\
Agents evalkit tracks ('which agents are there', 'the support chatbot'), with the id the \
run tools take.

Use when you need an agent's id (`name@workspace`) before calling find_runs, get_runs,
get_run_stats or get_trajectory.

Args:
  workspace: Only agents in this workspace (e.g. "prod").
  include_archived: Also list archived agents. Default false.

Returns:
  {"data": [{"id": "name@workspace", "name", "workspace", "workspace_type": "shared"|"personal",
    "trajectory_source": "atif"|"otel", "driver", "archived"}], "meta": {"count"}}
  An empty `data` means no agent matched the filter."""

FIND_RUNS = f"""\
Agent sessions ('chat sessions last week', 'sessions by user u-123', 'failed eval runs'), newest \
first, in a time window.

Sibling tools — pick the right one:
  - For metrics on runs you already have ids for -> get_runs
  - For totals, daily series or per-tool error counts over a window -> get_run_stats
    (then the sessions behind one tool's errors -> find_runs with tool_error)
  - For one run's step-by-step record -> get_trajectory

Filters and lists runs; it does not rank. Without `include` a row is the run listing only; with
`include=["metrics"]` each row also carries the run's metrics (reads every trajectory: slower).

Args:
{_RUN_FILTER_ARGS}
  timezone: IANA name for date-only bounds. Default "UTC". Pass get_run_stats' value to read
    the same window.
  tool_error: Only runs where this tool errored, as named in get_run_stats' "tool" rows
    (e.g. "search_docs"). Implies include=["metrics"]: each row's
    `metrics.tool_errors_by_tool` has the count and a representative message. Every call reads
    the window's trajectories again, up to 5000 runs, within the call's time budget, so pages
    can differ: `meta.total` counts the matching runs plus the runs not read, and each run not
    read is a row with `error` and `detail`. Take one page and pass those rows' ids to get_runs
    rather than paging on.
  failed_only: Only runs with a failure (a step marked `extra.is_error`: a turn that timed
    out, a provider error, a crash). Implies include=["metrics"]; add "turns" for each turn's
    error. Same window read as tool_error; combine the two freely.
  include: Any of "metrics", "turns", "tool_calls". Default none. "tool_calls" is what the
    agent called and with which arguments — enough to classify a run without its trajectory.
  limit: 1-5000 for the listing alone, 1-200 with include, tool_error or failed_only. Default 50.
    To read a large window, list it in one call, then get_runs for the ids in batches.
  offset: Pass `meta.next_offset` from the prior page while `meta.has_more` is true.

Returns:
  {{"data": [{{
{_RUN_ROW}
  }}], "meta": {{"count", "total", "has_more", "next_offset"?, "note"?}}}}
  `meta.note` (`partial_coverage`) says the agent's harness was unreachable (rows are evalkit's
  stored runs only), the window held more runs than were read, or some trajectories could not be
  read within the call's time budget. A row that could not be read carries `error` and
  `detail`: ask for it again with get_runs. Empty `data` means no run in the window.

Errors:
  - `not_found` — unknown agent.
  - `invalid_agent_id` — not a `name@workspace` id; take it from find_agents.
  - `invalid_date_format` / `invalid_range` — bad or inverted dates.
  - `invalid_source` / `invalid_include` / `invalid_limit` / `invalid_offset`.
  - `harness_timeout` — listing the agent's runs took the whole time budget; narrow the window."""

GET_RUNS = f"""\
Run metrics by id ('cost of run X', 'why did this session fail'): turns, failures, duration, \
tokens, tool errors.

Sibling tools — pick the right one:
  - To find run ids in a window or by user -> find_runs
  - For the raw steps (reasoning, tool arguments and results) -> get_trajectory

Use after find_runs, get_experiment_runs or a run id the user gave. One row per requested id,
in input order. A turn is one user message and everything the agent did until the next; a turn
the harness marked failed (`Step.extra.is_error`) has status "failed".

Args:
  agent: Agent id, `name@workspace`, from `find_agents`.
  run_ids: 1-200 run ids.
  include: Any of "metrics", "turns", "tool_calls". Default ["metrics"].

Returns:
  {{"data": [{{
{_RUN_ROW}
  }}], "meta": {{"count", "matched", "unmatched", "failed"?}}}}
  An unknown id is a row with `note: {{"code": "not_found"}}` and counts in `meta.unmatched`;
  a run whose trajectory could not be read carries `error` and `detail` and counts in
  `meta.failed` (left out when nothing failed). `cost_source` "estimated" means evalkit
  priced the tokens; "reported" means the agent's harness did.

Errors:
  - `not_found` — unknown agent.
  - `invalid_agent_id` — not a `name@workspace` id; take it from find_agents.
  - `no_ids_provided` / `too_many_ids` / `invalid_include`.
  - `invalid_run_id` — a run id that is not one plain path segment."""

GET_RUN_STATS = f"""\
Agent usage KPIs ('failures per day', 'which tools error most', 'spend by user'): \
sessions, failures, latency, cost, tool errors — total or by day, tool, user or source.

Sibling tools — pick the right one:
  - For the individual runs behind a number -> find_runs with the same filters and
    include=["metrics"]
  - For one run in depth -> get_runs

Reads every run's trajectory in the window, newest first, up to `max_runs`, within a time
budget; narrow the window rather than raising it. Runs not read count in `meta.unavailable`,
with a `partial_coverage` note in `meta.note`.

Args:
{_RUN_FILTER_ARGS}
    Date-only bounds are read in `timezone`.
  group_by: "none" (one totals row, default), "day", "tool", "user" or "source", or a list
    of them: one call reads the window once and returns every grouping's rows, each row's
    `group` naming its grouping (`{{}}` for "none"). Ask for all the groupings you need at once.
    "tool" has one row per tool that errored at least once, counting only the runs where it
    errored; tools that never errored have no row.
  timezone: IANA name for day buckets and date-only bounds. Default "UTC".
  max_runs: 1-5000. Default 500.

Returns:
  {{"data": [{{"group": {{}} | {{"day"}} | {{"tool"}} | {{"user"}} | {{"source"}},
    "runs", "runs_with_failure" (runs with any failure), "turns", "failed_turns", "failures",
    "cost": {{"value", "unit": "currency", "currency": "USD"}},
    "runs_with_estimated_cost", "runs_without_cost", "turn_p50_seconds", "turn_p90_seconds",
    "tool_errors", "runs_with_tool_errors", "sample_error"? (tool rows)}}],
   "meta": {{"count", "group_by": [...], "timezone", "runs_considered", "runs_in_window", "capped",
     "unavailable", "note"?}}}}
  `meta.capped` true means the window held more runs than `max_runs`. Empty `data` means no run
  in the window.

Errors:
  - `not_found` — unknown agent id.
  - `invalid_agent_id` — not a `name@workspace` id; take it from find_agents.
  - `invalid_group_by` / `invalid_source` / `invalid_timezone` / `invalid_date_format` /
    `invalid_range` / `invalid_limit` (max_runs).
  - `harness_timeout` — listing the agent's runs took the whole time budget; narrow the window."""

GET_TRAJECTORY = """\
What an agent did in a run ('show run X's tool calls', 'which call failed'): messages, \
reasoning, tool calls and results, per-step tokens and cost.

Use after find_runs or get_runs. Read get_runs' turns first; page here for the detail. Long text
is clipped to `max_chars` per field.

Args:
  agent: Agent id, `name@workspace`.
  run_id: One run id.
  subagent: Page this subagent's trajectory instead (index from `meta.subagents`).
  max_chars: 100-50000 per text field. Default 2000.
  limit: Steps per page, 1-100. Default 20.
  offset: Pass `meta.next_offset` while `meta.has_more` is true.

Returns:
  {"data": [{"step_id", "timestamp", "source": "user"|"agent"|"system", "message",
    "reasoning_content", "tool_calls": [{"tool_call_id", "function_name", "arguments"}],
    "observation": {"results": [{"source_call_id", "content", "extra": {"is_error"?, ...}}]},
    "metrics": {"prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd"},
    "model_name", "extra"}],
   "meta": {"count", "total", "has_more", "next_offset"?, "schema_version", "session_id",
     "trajectory_id", "agent", "final_metrics", "subagents": [{"index", "trajectory_id", "agent",
     "steps"}]}}
  Steps are ATIF v1.8.

Errors:
  - `not_found` — unknown agent, run or subagent index.
  - `invalid_limit` / `invalid_offset` / `invalid_max_chars`.
  - `invalid_run_id` — a run id that is not one plain path segment."""

GET_RUN_FILES = """\
Files one run produced: reports, spreadsheets, decks and full tool outputs it saved.

Use after find_runs or get_runs; read one with download_run_file.

Args:
  agent: Agent id, `name@workspace`.
  run_id: One run id.

Returns:
  {"data": [{"path", "size", "content_type"}], "meta": {"count"}}
  Empty `data` means the run saved no files.

Errors:
  - `not_found` — unknown agent or run, or its files are gone.
  - `invalid_agent_id` / `invalid_run_id` — an id not of the shape find_agents / find_runs give."""

DOWNLOAD_RUN_FILE = """\
Raw text of one file a run produced, a window of characters at a time.

Use after get_run_files.

Args:
  agent: Agent id, `name@workspace`.
  run_id: One run id.
  path: A path from get_run_files.
  max_chars: Characters per call, 1-200000. Default 20000.
  offset: Character offset. Pass `meta.next_offset` while `meta.has_more` is true.

Returns:
  {"data": [{"path", "size" (bytes), "text"}], "meta": {"count", "total" (characters),
    "has_more", "next_offset"?}}
  A binary file returns its row without `text` and with
  `note: {"code": "not_applicable", "message"}`.

Errors:
  - `not_found` — unknown agent, run or path.
  - `invalid_max_chars` / `invalid_offset`.
  - `invalid_agent_id` / `invalid_run_id` — an id not of the shape find_agents / find_runs give."""

FIND_EXPERIMENTS = """\
Eval experiments ('the latest eval', 'failed experiments'), newest first, with progress and \
mean score per evaluator.

Sibling tools — pick the right one:
  - For one experiment's scorecard -> get_experiments with include=["scorecard"]
  - For per-case results and judge explanations -> get_experiment_runs

Args:
  workspace: Only experiments in this workspace (e.g. "prod").
  status: "running", "evaluating", "completed" or "failed".
  limit: 1-100. Default 20.
  offset: Pass `meta.next_offset` while `meta.has_more` is true.

Returns:
  {"data": [{"id", "number", "workspace", "dataset", "agent", "created_by", "status",
    "status_error", "repetitions", "started_at", "ended_at", "progress", "scores", ...}],
   "meta": {"count", "total": null, "has_more", "next_offset"?}}

Errors:
  - `not_found` — unknown workspace.
  - `invalid_status` / `invalid_limit` / `invalid_offset`."""

GET_EXPERIMENTS = """\
Experiments by id ('scorecard of experiment 12', 'how did eval 40 score'): progress and, on \
request, the scorecard: buckets, evaluator means, gauges.

Sibling tools — pick the right one:
  - For aggregate scores of an experiment -> this tool with include=["scorecard"]
  - For why one case failed (its answer, scores and judge explanations) -> get_experiment_runs

Use after find_experiments or an id the user gave. One row per requested id, in input order.

Args:
  experiment_ids: 1-20 experiment ids.
  include: ["scorecard"] to add each experiment's scorecard. Default none.

Returns:
  {"data": [{"id", "number", "workspace", "dataset", "agent", "status", "started_at",
    "ended_at", "progress", "scorecard"?, ...}],
   "meta": {"count", "matched", "unmatched"}}
  An unknown id is a row with `note: {"code": "not_found"}`.

Errors:
  - `no_ids_provided` / `too_many_ids` / `invalid_include`."""

GET_EXPERIMENT_RUNS = """\
Per-case eval results ('why did case X fail', 'which cases regressed'): answer, each \
evaluator's score, label and explanation.

Sibling tools — pick the right one:
  - For why one case failed -> this tool
  - For an experiment's aggregate scores -> get_experiments with include=["scorecard"]

Use after find_experiments. A row's `id` with `meta.agent` opens the run in get_runs or
get_trajectory.

Args:
  experiment_id: One experiment id.
  failed_only: Only runs with an error or a score below 1. Default false.
  limit: 1-200. Default 50.
  offset: Pass `meta.next_offset` while `meta.has_more` is true.

Returns:
  {"data": [{"id", "case_id", "repetition", "status", "error", "answer", "started_at",
    "ended_at", "metrics", "evaluations": [{"name", "kind", "value", "label", "explanation",
    "error"}]}],
   "meta": {"count", "total", "has_more", "next_offset"?, "agent"}}

Errors:
  - `not_found` — unknown experiment.
  - `invalid_limit` / `invalid_offset`."""
