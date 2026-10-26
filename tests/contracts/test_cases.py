from pathlib import Path

import pytest
from pydantic import ValidationError

from evalkit.contracts.cases import (
    Case,
    Overlay,
    content_hash,
    is_case_yaml,
    load_cases_from_yaml,
    load_registry,
    overlay_hash,
)

DATA = Path(__file__).parent.parent / "data"


def test_load_basic_defaults_merge():
    cases, overlays = load_cases_from_yaml(DATA / "cases_basic.yaml")
    assert overlays == []
    assert [c.id for c in cases] == ["sample-case-one", "sample-case-two"]
    one, two = cases
    assert one.tags == ["finance", "equity", "starbucks"]
    assert one.input == {"locale": "en", "question": "How did the store count change?"}
    assert one.evaluators.output.max_response_time is not None
    assert one.evaluators.output.max_response_time.max_seconds == 600
    assert one.evaluators.custom == {"required_tool_calls": {"tools": ["retrieve_passages"]}}
    assert two.input["locale"] == "hi"
    assert two.evaluators.output.max_response_time.max_seconds == 120
    assert two.fixtures == ["some-doc.pdf"]


def test_unknown_defaults_key_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("defaults:\n  judge_model: gpt-x\ncases:\n- id: a\n  input: {question: q}\n")
    with pytest.raises(ValueError, match="judge_model"):
        load_cases_from_yaml(p)


def test_unknown_case_key_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("- id: a\n  input: {question: q}\n  judge_model: gpt-x\n")
    with pytest.raises(Exception, match="judge_model"):
        load_cases_from_yaml(p)


def test_overlay_file_parses():
    cases, overlays = load_cases_from_yaml(DATA / "cases_overlay.yaml")
    assert cases == []
    assert len(overlays) == 1
    ov = overlays[0]
    assert ov.extends == "sample-case-one"
    assert ov.tags == ["client-priority"]
    assert set(ov.evaluators.custom) == {"required_tool_calls", "tool_trajectory_match"}


def test_overlay_with_input_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "overlays:\n- extends: a\n  agents: [support-bot]\n  input: {question: q}\n"
        "  evaluators: {custom: {x: {}}}\n"
    )
    with pytest.raises(Exception, match="input"):
        load_cases_from_yaml(p)


def test_overlay_with_builtin_evaluator_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        "overlays:\n- extends: a\n  agents: [support-bot]\n  evaluators:\n    output:\n"
        "      answer_rubrics: {rubrics: [{rubric: r, type: factual_retrieval}]}\n"
    )
    with pytest.raises(Exception, match="output"):
        load_cases_from_yaml(p)


def test_overlay_requires_agents(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text("overlays:\n- extends: a\n  evaluators: {custom: {x: {}}}\n")
    with pytest.raises(ValidationError, match="agents"):
        load_cases_from_yaml(p)


def test_overlay_agents_from_defaults(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text(
        "defaults: {agents: [support-bot]}\n"
        "overlays:\n- extends: a\n  evaluators: {custom: {x: {}}}\n"
        "- extends: b\n  agents: [research-agent]\n  evaluators: {custom: {x: {}}}\n"
    )
    _, overlays = load_cases_from_yaml(p)
    assert [ov.agents for ov in overlays] == [["support-bot"], ["research-agent"]]


def test_defaults_agents_do_not_touch_cases(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("defaults: {agents: [support-bot]}\ncases:\n- id: a\n  input: {question: q}\n")
    [case], _ = load_cases_from_yaml(p)
    assert case.id == "a"


def test_overlay_agent_must_be_a_bare_name(tmp_path):
    p = tmp_path / "o.yaml"
    p.write_text(
        "overlays:\n- extends: a\n  agents: [support-bot@akshat]\n  evaluators: {custom: {x: {}}}\n"
    )
    with pytest.raises(ValidationError, match="agents"):
        load_cases_from_yaml(p)


def test_overlay_hash_sees_agents():
    base = {"extends": "a", "evaluators": {"custom": {"x": {}}}}
    first = Overlay.model_validate({**base, "agents": ["support-bot"]})
    second = Overlay.model_validate({**base, "agents": ["research-agent"]})
    assert overlay_hash(first) != overlay_hash(second)


def test_content_hash_stable_and_sensitive():
    cases, _ = load_cases_from_yaml(DATA / "cases_basic.yaml")
    h1 = content_hash(cases[0])
    h2 = content_hash(cases[0])
    assert h1 == h2 and len(h1) == 16
    mutated = cases[0].model_copy(deep=True)
    mutated.evaluators.output.answer_rubrics.rubrics[0].rubric = "Something else."
    assert content_hash(mutated) != h1


def test_overlay_hash():
    _, overlays = load_cases_from_yaml(DATA / "cases_overlay.yaml")
    assert len(overlay_hash(overlays[0])) == 16


def test_trajectory_rubrics_is_a_list_of_sentences():
    from evalkit.contracts.cases import Case

    case = Case.model_validate(
        {
            "id": "a",
            "input": {"question": "q"},
            "evaluators": {
                "trajectory": {
                    "trajectory_rubrics": {
                        "rubrics": [
                            "Retrieves FY2024 revenue as $3,522,776,000.",
                            "Rounds the final figure to the nearest basis point.",
                        ]
                    }
                }
            },
        }
    )
    assert case.evaluators.trajectory.trajectory_rubrics is not None
    assert case.evaluators.trajectory.trajectory_rubrics.rubrics == [
        "Retrieves FY2024 revenue as $3,522,776,000.",
        "Rounds the final figure to the nearest basis point.",
    ]
    assert case.evaluators.as_metadata()["trajectory"] == {
        "trajectory_rubrics": {
            "rubrics": [
                "Retrieves FY2024 revenue as $3,522,776,000.",
                "Rounds the final figure to the nearest basis point.",
            ]
        }
    }


@pytest.mark.parametrize("rubrics", [[], [""], ["ok", ""]])
def test_trajectory_rubrics_rejects_empty(rubrics):
    from evalkit.contracts.cases import TrajectoryRubrics

    with pytest.raises(ValueError):
        TrajectoryRubrics.model_validate({"rubrics": rubrics})


def test_trajectory_rubrics_is_gone():
    from evalkit.contracts.cases import Case

    with pytest.raises(ValueError, match="trajectory_rubrics"):
        Case.model_validate(
            {
                "id": "a",
                "input": {"question": "q"},
                "evaluators": {"trajectory": {"trajectory_rubrics": {"expected": ["x"]}}},
            }
        )


def test_registry_loads(tmp_path):
    p = tmp_path / "fixtures.yaml"
    p.write_text(
        f"fixtures:\n- key: doc.pdf\n  sha256: {'a' * 64}\n  context: A ten-page annual report.\n"
    )
    reg = load_registry(p)
    assert [e.key for e in reg.fixtures] == ["doc.pdf"]
    assert reg.fixtures[0].context == "A ten-page annual report."


@pytest.mark.parametrize("key", ["a/b.pdf", "", ".", "..", "a\\b.pdf"])
def test_registry_rejects_non_flat_key(tmp_path, key):
    p = tmp_path / "fixtures.yaml"
    p.write_text(f"fixtures:\n- key: '{key}'\n  sha256: {'a' * 64}\n  context: x\n")
    with pytest.raises(ValidationError):
        load_registry(p)


def test_registry_rejects_short_digest(tmp_path):
    p = tmp_path / "fixtures.yaml"
    p.write_text("fixtures:\n- key: doc.pdf\n  sha256: abc123\n  context: x\n")
    with pytest.raises(ValidationError):
        load_registry(p)


def test_is_case_yaml(tmp_path):
    assert is_case_yaml(tmp_path / "cases.yaml")
    assert is_case_yaml(tmp_path / "sub" / "more.yml")
    assert not is_case_yaml(tmp_path / "fixtures.yaml")
    assert not is_case_yaml(tmp_path / "manifest.yaml")
    assert is_case_yaml(tmp_path / "fixtures" / "weird.yaml")
    assert not is_case_yaml(tmp_path / "doc.csv")


def test_content_hash_covers_files():
    case = Case.model_validate({"id": "c", "input": {"question": "q"}, "fixtures": ["doc.pdf"]})
    a = content_hash(case, [{"key": "doc.pdf", "sha256": "1" * 64, "context": "one"}])
    b = content_hash(case, [{"key": "doc.pdf", "sha256": "1" * 64, "context": "two"}])
    c = content_hash(case, [{"key": "doc.pdf", "sha256": "2" * 64, "context": "one"}])
    assert len({a, b, c}) == 3


def test_registry_with_no_entries_loads_empty(tmp_path):
    p = tmp_path / "fixtures.yaml"
    p.write_text("fixtures:\n")
    assert load_registry(p).fixtures == []


SUPPORT_BOT_TRAJECTORY_BLOCK = {
    "required_tools_called": {"tools": ["get_instrument_details", "get_bond_trades"]},
    "tool_trajectory_match": {
        "ordered": False,
        "expected": [
            {
                "name": "get_bond_trades",
                "args": {"isins": "INE831R07540"},
                "args_mode": "substring",
            },
            {
                "name": "get_bond_vwap",
                "args": {"series": "SDL"},
                "match_overrides": {"series": "substring"},
            },
        ],
    },
}


def test_support_bot_tool_trajectory_config_loads_unchanged():
    case = Case.model_validate(
        {
            "id": "a",
            "input": {"question": "q"},
            "evaluators": {"trajectory": SUPPORT_BOT_TRAJECTORY_BLOCK},
        }
    )
    metadata = case.evaluators.as_metadata()["trajectory"]
    assert metadata["required_tools_called"] == {
        "tools": ["get_instrument_details", "get_bond_trades"]
    }
    match = metadata["tool_trajectory_match"]
    assert match["ordered"] is False
    assert match["expected"][0]["args_mode"] == "substring"
    assert match["expected"][1] == {
        "name": "get_bond_vwap",
        "args": {"series": "SDL"},
        "args_mode": "subset",
        "match_overrides": {"series": "substring"},
    }


@pytest.mark.parametrize(
    "config",
    [
        {"expected": []},
        {"expected": [{"name": ""}]},
        {"expected": [{"name": "a", "args_mode": "fuzzy"}]},
        {"expected": [{"name": "a"}], "agents": ["support-bot"]},
        {"expected": [{"name": "a", "args": {"x": 1}, "match_overrides": {"y": "substring"}}]},
        {
            "expected": [
                {
                    "name": "a",
                    "args": {"x": 1},
                    "args_mode": "exact",
                    "match_overrides": {"x": "substring"},
                }
            ]
        },
    ],
)
def test_tool_trajectory_match_rejects_bad_config(config):
    from evalkit.contracts.cases import ToolTrajectoryMatch

    with pytest.raises(ValidationError):
        ToolTrajectoryMatch.model_validate(config)


@pytest.mark.parametrize("config", [{"tools": []}, {"tools": [""]}, {"tools": ["a"], "x": 1}])
def test_required_tools_called_rejects_bad_config(config):
    from evalkit.contracts.cases import RequiredToolsCalled

    with pytest.raises(ValidationError):
        RequiredToolsCalled.model_validate(config)


def test_overlay_carries_tool_trajectory_evaluators(tmp_path):
    p = tmp_path / "ov.yaml"
    p.write_text(
        "overlays:\n- extends: a\n  agents: [support-bot]\n  evaluators:\n    trajectory:\n"
        "      required_tools_called: {tools: [get_news]}\n"
    )
    _, [overlay] = load_cases_from_yaml(p)
    assert overlay.evaluators.trajectory.required_tools_called.tools == ["get_news"]
    assert overlay.evaluators.custom == {}


def test_overlay_hash_sees_trajectory_block(tmp_path):
    p = tmp_path / "ov.yaml"
    body = (
        "overlays:\n- extends: a\n  agents: [support-bot]\n  evaluators:\n    trajectory:\n"
        "      required_tools_called: {{tools: [{tool}]}}\n"
    )
    p.write_text(body.format(tool="get_news"))
    _, [first] = load_cases_from_yaml(p)
    p.write_text(body.format(tool="get_bond_trades"))
    _, [second] = load_cases_from_yaml(p)
    assert overlay_hash(first) != overlay_hash(second)


def test_overlay_rejects_trajectory_rubrics(tmp_path):
    p = tmp_path / "ov.yaml"
    p.write_text(
        "overlays:\n- extends: a\n  agents: [support-bot]\n  evaluators:\n    trajectory:\n"
        "      trajectory_rubrics: {rubrics: [r]}\n"
    )
    with pytest.raises(ValidationError, match="trajectory_rubrics"):
        load_cases_from_yaml(p)


def test_overlay_needs_some_evaluator(tmp_path):
    p = tmp_path / "ov.yaml"
    p.write_text("overlays:\n- extends: a\n  agents: [support-bot]\n  evaluators: {}\n")
    with pytest.raises(ValidationError, match="custom or trajectory"):
        load_cases_from_yaml(p)


def test_content_hash_ignores_where_a_fixture_was_resolved_from():
    from evalkit.contracts.cases import Case, content_hash

    case = Case(id="c", input={"question": "q"}, fixtures=["a.csv"])
    entry = {"key": "a.csv", "sha256": "a" * 64, "context": "ctx"}
    assert content_hash(case, [entry]) == content_hash(case, [{**entry, "workspace": "akshat"}])
    assert content_hash(case, [entry]) != content_hash(case, [{**entry, "sha256": "b" * 64}])
