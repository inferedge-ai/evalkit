// Score, gauge and bucket names carry compressed meaning ("accuracy" is not
// precision here) — these one-liners feed the ⓘ tooltips so a reader never has
// to remember the formulas. Unknown names (custom evaluators) get no entry and
// render no tooltip.

const ANSWER_RUBRIC_FAMILY =
  "Judge-graded checklist over the final answer: each rubric is marked satisfied, incorrect or absent, and these rates summarise the verdicts.";

const TRAJECTORY_RUBRIC_FAMILY =
  "Satisfied ÷ total over the case's trajectory rubrics — checkpoints the recorded steps or final answer must show. Each is judged satisfied or unsatisfied; a long trajectory is judged in parts and a rubric counts when any part shows it.";

export const GLOSSARY: Record<string, string> = {
  answer_rubric: ANSWER_RUBRIC_FAMILY,
  answer_rubrics: ANSWER_RUBRIC_FAMILY,
  answer_rubric_score:
    "Final content score: satisfied rubrics ÷ all rubrics. Equals coverage × accuracy.",
  answer_rubric_coverage:
    "Attempted ÷ total: how many rubrics the answer addressed at all, right or wrong.",
  answer_rubric_accuracy:
    "Satisfied ÷ attempted: of what the answer addressed, how much was right. Extra content the rubrics never asked about is not penalised.",
  answer_rubric_citation:
    "Correct source citations ÷ eligible. A rubric's source counts only where the answer actually stated the fact — an unstated fact had no chance to be cited.",
  answer_rubric_factual_retrieval:
    "Satisfied ÷ total on factual_retrieval rubrics: facts read straight from the source data.",
  answer_rubric_derived_computation:
    "Satisfied ÷ total on derived_computation rubrics: figures that need calculation from the source data.",
  answer_rubric_analysis_interpretation:
    "Satisfied ÷ total on analysis_interpretation rubrics: reasoning or judgement over the data.",
  trajectory_rubrics: TRAJECTORY_RUBRIC_FAMILY,
  trajectory_rubric_score: TRAJECTORY_RUBRIC_FAMILY,
  tool_trajectory_match:
    "Found ÷ expected over the case's expected tool calls, matched by tool name and arguments, each to at most one actual call. With ordered, they must also appear in that order.",
  required_tools_called:
    "Called ÷ required: how many of the case's required tools the agent called at least once. Extra calls are not penalised.",
  presentation_judge:
    "Judge score on how the reply presents itself — structure and formatting — against the case's presentation config.",
  max_response_time:
    "Pass/fail: did the run finish within the case's time limit.",

  "bucket:content":
    "Mean answer_rubric_score across cases. The other answer_rubric rates are diagnostics on this score, not bucket members.",
  "bucket:trajectory":
    "Mean of the trajectory evaluators' scores across cases: trajectory_rubric_score, tool_trajectory_match and required_tools_called, where configured.",
  "bucket:presentation": "Mean presentation_judge score across cases.",
  "bucket:efficiency": "Mean max_response_time score across cases.",
  "bucket:custom": "Mean of all custom-evaluator scores across cases.",

  gauge_duration_seconds: "Wall-clock time of the run.",
  gauge_input_tokens:
    "Input tokens the agent consumed, from the trajectory's token usage.",
  gauge_output_tokens: "Output tokens the agent produced.",
  gauge_cached_tokens: "Input tokens served from the provider's prompt cache.",
  gauge_agent_cost_usd:
    "Estimated cost of the agent's own LLM usage, priced from its token usage.",
  gauge_judge_cost_usd: "evalkit's own judge spend scoring this run.",
  gauge_total_tool_calls: "Tool calls the agent made during the run.",
  gauge_errored_tool_calls: "Tool calls whose result came back as an error.",
  gauge_subagents: "Subagent trajectories the run spawned.",
  gauge_turns: "Assistant turns in the trajectory.",
};

export function describeTerm(name: string): string | undefined {
  return GLOSSARY[name];
}
