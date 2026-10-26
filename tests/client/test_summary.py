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
