import json
from pathlib import Path

import pytest

from evalkit.contracts.atif import (
    ACCEPTED_SCHEMA_VERSIONS,
    Trajectory,
    TrajectoryValidationError,
    subagent_link_warnings,
    validate_trajectory,
)

DATA = Path(__file__).parent.parent / "data"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def test_minimal_validates():
    doc = validate_trajectory(load("atif_minimal.json"))
    assert isinstance(doc, Trajectory)
    assert len(doc.steps) == 2
    assert doc.steps[0].source == "user"


def test_full_validates():
    doc = validate_trajectory(load("atif_full.json"))
    assert doc.agent is not None and doc.agent.name == "myagent"
    assert doc.steps[1].reasoning_content is not None
    assert doc.steps[1].tool_calls is not None
    assert doc.steps[1].tool_calls[0].function_name == "web_search"
    assert doc.steps[1].observation is not None
    assert doc.steps[1].observation.results[1].extra == {"is_error": True}
    assert doc.steps[1].metrics is not None
    assert doc.steps[1].metrics.prompt_tokens == 1200
    assert doc.final_metrics is not None
    assert doc.final_metrics.total_cost_usd == 0.02
    assert doc.subagent_trajectories is not None
    assert len(doc.subagent_trajectories) == 2
    assert doc.subagent_trajectories[0].agent.name == "researcher"


def test_wrong_schema_version_rejected():
    data = load("atif_minimal.json")
    data["schema_version"] = "ATIF-v0.9"
    with pytest.raises(TrajectoryValidationError, match="schema_version"):
        validate_trajectory(data)
    assert "ATIF-v1.8" in ACCEPTED_SCHEMA_VERSIONS


def test_missing_source_rejected():
    data = load("atif_minimal.json")
    del data["steps"][0]["source"]
    with pytest.raises(TrajectoryValidationError, match="source"):
        validate_trajectory(data)


def test_unknown_fields_tolerated():
    data = load("atif_minimal.json")
    data["future_field"] = {"x": 1}
    data["steps"][0]["future_step_field"] = "y"
    doc = validate_trajectory(data)
    assert doc.steps[0].step_id == 1


def test_subagent_link_fields_parse():
    doc = {
        "schema_version": "ATIF-v1.8",
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "tool_calls": [{"tool_call_id": "c1", "function_name": "Agent", "arguments": {}}],
                "observation": {
                    "results": [
                        {
                            "source_call_id": "c1",
                            "subagent_trajectory_ref": [{"trajectory_id": "sub-1"}],
                        }
                    ]
                },
            }
        ],
        "subagent_trajectories": [
            {"schema_version": "ATIF-v1.8", "trajectory_id": "sub-1", "steps": []}
        ],
    }
    trajectory = validate_trajectory(doc)
    [result] = trajectory.steps[0].observation.results
    assert result.subagent_trajectory_ref[0].trajectory_id == "sub-1"
    assert trajectory.subagent_trajectories[0].trajectory_id == "sub-1"
    assert subagent_link_warnings(trajectory) == []


def test_subagent_link_warnings_name_the_gap():
    doc = {
        "schema_version": "ATIF-v1.8",
        "steps": [],
        "subagent_trajectories": [
            {"schema_version": "ATIF-v1.8", "steps": []},
            {"schema_version": "ATIF-v1.8", "steps": []},
        ],
    }
    assert subagent_link_warnings(validate_trajectory(doc)) == [
        "2 embedded subagent(s) have no trajectory_id (required since ATIF-v1.7)"
    ]
