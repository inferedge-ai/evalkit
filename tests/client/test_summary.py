from evalkit.client.summary import summary_lines

REPORT = {
    "name": "#1",
    "experiment_id": 1,
    "dataset": "acme@admin",
    "buckets": {"content": 1.0, "trajectory": 0.5, "custom": None},
    "evaluators": [{"name": "answer_rubric_score", "score": 1.0, "cases": 5, "errors": 0}],
    "cases": [],
    "gauges": [
        {"name": "wall_time_s", "mean": 8.440000000000001, "median": 7.0},
        {"name": "gauge_agent_cost_usd", "mean": 0.00175425, "median": 0.0},
    ],
}


def test_summary_hides_buckets_without_scores_and_rounds_gauges():
    lines = summary_lines(REPORT)
    assert "  content        1.00" in lines and "  trajectory     0.50" in lines
    assert not any("custom" in line for line in lines)
    assert any(line.endswith("mean=8.44 median=7") for line in lines)
    assert any(line.endswith("mean=0.00175 median=0") for line in lines)


def test_summary_counts_one_case_in_the_singular():
    stats = [
        {"name": "one", "score": 1.0, "cases": 1, "errors": 0},
        {"name": "two", "score": 1.0, "cases": 2, "errors": 0},
    ]
    lines = summary_lines({**REPORT, "evaluators": stats})
    assert any(line.endswith("(1 case)") for line in lines)
    assert any(line.endswith("(2 cases)") for line in lines)


def test_summary_without_any_scored_bucket_has_no_buckets_heading():
    lines = summary_lines({**REPORT, "buckets": {"presentation": None}})
    assert "buckets:" not in lines


def _case(case_id, overall, **scores):
    return {
        "case_id": case_id,
        "overall": overall,
        "scores": scores,
        "errors": {},
        "run_errors": [],
    }


def test_summary_with_baseline_shows_fixed_new_and_removed_cases_and_deltas():
    base = {
        **REPORT,
        "experiment_id": "1",
        "name": "#1",
        "buckets": {"content": 0.5},
        "evaluators": [{"name": "answer_rubric_score", "score": 0.5, "cases": 2, "errors": 0}],
        "cases": [_case("fixed", 0.0), _case("same", 1.0), _case("gone", 1.0)],
    }
    compare = {
        **REPORT,
        "experiment_id": "2",
        "name": "#2",
        "buckets": {"content": 1.0},
        "evaluators": [{"name": "answer_rubric_score", "score": 1.0, "cases": 2, "errors": 0}],
        "cases": [_case("fixed", 1.0), _case("same", 1.0), _case("added", 1.0)],
    }
    lines = summary_lines(compare, base)
    text = "\n".join(lines)
    assert "vs experiment 1  #1 of acme@admin" in lines
    assert "  content        1.00  Δ+0.50" in lines
    assert "(2 cases)  Δ+0.50" in text
    assert "  improved (1):" in lines
    assert any("fixed" in line and "0.00 → 1.00  Δ+1.00" in line for line in lines)
    assert any("added" in line and "  -  → 1.00" in line for line in lines)
    assert any("gone" in line and "1.00 →   - " in line for line in lines)
    assert "  unchanged: 1" in lines
    assert "failing cases" not in text


def test_summary_lists_a_case_with_one_failing_evaluator_above_the_overall_bar():
    report = {**REPORT, "cases": [_case("c1", 0.9, answer_rubric_score=0.67, other=1.0)]}
    lines = summary_lines(report)
    assert any(line.endswith("0.90  (answer_rubric_score 0.67)") for line in lines)


def test_summary_with_baseline_lists_unscored_cases_and_hides_tiny_deltas():
    base = {
        **REPORT,
        "buckets": {"content": 0.8333},
        "cases": [_case("fixed", None), _case("broken", None)],
    }
    compare = {
        **REPORT,
        "buckets": {"content": 0.8341},
        "cases": [_case("fixed", 1.0), _case("broken", None)],
    }
    lines = summary_lines(compare, base)
    assert "  content        0.83" in lines
    assert "  unscored (2):" in lines
    assert any("fixed" in line and "  -  → 1.00" in line for line in lines)
    assert any("broken" in line for line in lines)
    assert "  unchanged: 0" in lines


def test_summary_with_baseline_shows_the_base_case_count_when_it_differs():
    stat = {"name": "answer_rubric_score", "score": 1.0, "errors": 0}
    base = {**REPORT, "evaluators": [{**stat, "cases": 2}]}
    compare = {**REPORT, "evaluators": [{**stat, "cases": 3}]}
    lines = summary_lines(compare, base)
    assert any(line.endswith("(3 cases, base 2)") for line in lines)


def test_summary_failing_list_ignores_rubric_diagnostics():
    report = {**REPORT, "cases": [_case("c1", 0.9, answer_rubric_citation=0.0)]}
    assert not any(line.startswith("failing cases") for line in summary_lines(report))
