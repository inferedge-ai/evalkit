from evalkit.contracts.cases import Overlay
from evalkit.core.overlays import (
    OverlayBlock,
    apply_overlay_blocks,
    incoming_overlay,
    overlay_clashes,
)

CASE = {"output": {"answer_rubrics": {"rubrics": []}}, "custom": {"x": {"a": 1}}}
BLOCKS = [
    OverlayBlock(
        source="client-cases",
        agents=["support-bot"],
        evaluators={
            "trajectory": {"required_tools_called": {"tools": ["t"]}},
            "custom": {"x": {"a": 2}},
        },
    ),
    OverlayBlock(source="fin-cases", agents=["research-agent"], evaluators={"custom": {"y": {}}}),
]


def test_matching_agent_gets_its_overlay_in_any_workspace():
    for agent in ("support-bot", "support-bot@akshat"):
        assert apply_overlay_blocks(CASE, BLOCKS, agent) == {
            "output": CASE["output"],
            "custom": {"x": {"a": 2}},
            "trajectory": {"required_tools_called": {"tools": ["t"]}},
        }


def test_other_agents_see_only_their_own_overlays():
    assert apply_overlay_blocks(CASE, BLOCKS, "research-agent") == {
        **CASE,
        "custom": {"x": {"a": 1}, "y": {}},
    }
    assert apply_overlay_blocks(CASE, BLOCKS, "other") == CASE


def test_no_overlays_is_the_case_config():
    assert apply_overlay_blocks(CASE, [], "support-bot") == CASE


def test_apply_leaves_case_config_alone():
    apply_overlay_blocks(CASE, BLOCKS, "support-bot")
    assert CASE["custom"] == {"x": {"a": 1}}


def test_incoming_overlay_keeps_only_set_families():
    overlay = Overlay.model_validate(
        {
            "extends": "case-1",
            "agents": ["support-bot"],
            "tags": ["p"],
            "evaluators": {"custom": {"x": {}}},
        }
    )
    incoming = incoming_overlay(overlay, "overlays.yaml")
    assert incoming.target_case_id == "case-1"
    assert incoming.agents == ["support-bot"] and incoming.tags == ["p"]
    assert incoming.evaluators == {"custom": {"x": {}}}
    assert incoming.source_file == "overlays.yaml"


EXISTING = {
    "case-1": [
        OverlayBlock(
            source="client-cases", agents=["support-bot"], evaluators={"custom": {"x": {}}}
        )
    ]
}


def overlay(agents: list[str], name: str) -> Overlay:
    return Overlay.model_validate(
        {"extends": "case-1", "agents": agents, "evaluators": {"custom": {name: {}}}}
    )


def test_same_agent_same_evaluator_from_another_source_clashes():
    [finding] = overlay_clashes([overlay(["support-bot", "other"], "x")], EXISTING)
    assert finding.code == "E-OVERLAY-CLASH" and finding.severity == "error"
    assert finding.case_id == "case-1"
    assert finding.message == "client-cases already sets custom.x for agents support-bot"


def test_different_agent_or_evaluator_or_no_other_source_does_not_clash():
    assert overlay_clashes([overlay(["research-agent"], "x")], EXISTING) == []
    assert overlay_clashes([overlay(["support-bot"], "y")], EXISTING) == []
    assert overlay_clashes([overlay(["support-bot"], "x")], {}) == []


def test_no_agent_recorded_gets_only_the_case_config():
    assert apply_overlay_blocks(CASE, BLOCKS, None) == CASE
    assert apply_overlay_blocks(CASE, BLOCKS, "") == CASE
