import hashlib

from evalkit.contracts.cases import Case, FixtureEntry, Overlay
from evalkit.contracts.harness import DEFAULT_INPUT_SCHEMA
from evalkit.core.lint import fixture_findings, has_errors, lint


def make_case(**overrides) -> Case:
    data = {
        "id": "case-1",
        "tags": ["finance"],
        "input": {"question": "What was FY24 revenue as of 2024-06-30?"},
        "evaluators": {
            "output": {
                "answer_rubrics": {
                    "rubrics": [
                        {"rubric": "States FY24 revenue of $1.2B.", "type": "factual_retrieval"},
                        {"rubric": "Notes the YoY change.", "type": "analysis_interpretation"},
                    ]
                }
            }
        },
    }
    data.update(overrides)
    return Case.model_validate(data)


def run_lint(cases=None, overlays=None, known=None, other=None):
    return lint(
        cases or [],
        overlays or [],
        known_custom_evaluators=known or set(),
        other_cases=other,
    )


def codes(findings):
    return [f.code for f in findings]


def test_clean_case_no_findings():
    assert run_lint([make_case()]) == []


def test_dup_id():
    findings = run_lint([make_case(), make_case()])
    assert "E-DUP-ID" in codes(findings)


def test_jargon_leak():
    case = make_case(
        evaluators={
            "trajectory": {
                "trajectory_rubrics": {"rubrics": ["Calls mcp__acme__query_bonds to fetch data."]}
            }
        }
    )
    findings = run_lint([case])
    assert "E-JARGON" in codes(findings)
    assert has_errors(findings)


def test_jargon_snake_call():
    case = make_case(
        evaluators={
            "trajectory": {"trajectory_rubrics": {"rubrics": ["Uses retrieve_passages(query)."]}}
        }
    )
    assert "E-JARGON" in codes(run_lint([case]))


def test_custom_unknown():
    case = make_case(evaluators={"custom": {"required_tool_calls": {"tools": ["x"]}}})
    findings = run_lint([case], known={"other"})
    assert "W-CUSTOM-UNKNOWN" in codes(findings)
    assert not has_errors(findings)
    assert run_lint([case], known={"required_tool_calls"}) == []


def test_reserved_tag():
    assert "E-RESERVED-TAG" in codes(run_lint([make_case(tags=["working:foo"])]))


def test_overlay_target_missing():
    overlay = Overlay.model_validate(
        {"extends": "nope", "agents": ["support-bot"], "evaluators": {"custom": {"x": {}}}}
    )
    findings = run_lint(overlays=[overlay], known={"x"}, other={"case-1": "finance"})
    assert [(f.code, f.message) for f in findings] == [
        ("E-OVERLAY-TARGET", "overlay extends a case id no other dataset in this workspace holds")
    ]


def test_overlay_ok():
    overlay = Overlay.model_validate(
        {"extends": "case-1", "agents": ["support-bot"], "evaluators": {"custom": {"x": {}}}}
    )
    assert run_lint(overlays=[overlay], known={"x"}, other={"case-1": "finance"}) == []


def test_w_undated():
    case = make_case(
        input={"question": "What is the revenue?"},
        evaluators={
            "output": {
                "answer_rubrics": {
                    "rubrics": [
                        {"rubric": "States revenue of $1.2 billion.", "type": "factual_retrieval"},
                        {"rubric": "Notes the trend.", "type": "analysis_interpretation"},
                    ]
                }
            }
        },
    )
    findings = run_lint([case])
    assert codes(findings) == ["W-UNDATED"]
    assert not has_errors(findings)


def test_w_thin_rubrics():
    case = make_case(
        evaluators={
            "output": {
                "answer_rubrics": {
                    "rubrics": [
                        {
                            "rubric": "States FY24 revenue of $1.2B as of 2024-06-30.",
                            "type": "factual_retrieval",
                        }
                    ]
                }
            }
        }
    )
    assert "W-THIN-RUBRICS" in codes(run_lint([case]))


def _fixture_case(case_id: str, *keys: str) -> Case:
    return Case.model_validate(
        {"id": case_id, "input": {"question": "q as of 2024-01-01"}, "fixtures": list(keys)}
    )


def _entry(key: str, data: bytes) -> FixtureEntry:
    return FixtureEntry(key=key, sha256=hashlib.sha256(data).hexdigest(), context="ctx")


def test_fixture_findings_unknown_key():
    findings = fixture_findings([_fixture_case("c1", "nope.pdf")], {})
    assert [(f.code, f.case_id) for f in findings] == [("E-FIXTURE-UNKNOWN-KEY", "c1")]


def test_fixture_findings_clean_and_unused():
    entries = {"doc.pdf": _entry("doc.pdf", b"v1"), "spare.pdf": _entry("spare.pdf", b"s")}
    findings = fixture_findings([_fixture_case("c1", "doc.pdf")], entries)
    assert [(f.severity, f.code, f.case_id) for f in findings] == [
        ("warning", "W-FIXTURE-UNUSED", "spare.pdf")
    ]
    assert fixture_findings([_fixture_case("c1", "doc.pdf")], entries, warn_unused=False) == []


def test_input_schema_errors_per_case():
    schema = {"type": "object", "required": ["source_type"]}
    findings = lint(
        [make_case(), make_case(id="case-2", input={"question": "q", "source_type": "news"})],
        [],
        known_custom_evaluators=set(),
        input_schema=schema,
    )
    hits = [f for f in findings if f.code == "E-INPUT-SCHEMA"]
    assert [(f.case_id, f.severity) for f in hits] == [("case-1", "error")]
    assert "source_type" in hits[0].message


def test_default_input_schema_requires_a_question():
    findings = lint(
        [make_case(), make_case(id="case-2", input={"text": "doc"})],
        [],
        known_custom_evaluators=set(),
        input_schema=DEFAULT_INPUT_SCHEMA,
    )
    hits = [f for f in findings if f.code == "E-INPUT-SCHEMA"]
    assert [f.case_id for f in hits] == ["case-2"]
    assert "'question' is a required property" in hits[0].message


def test_tool_names_in_tool_trajectory_evaluators_are_not_jargon():
    case = make_case(
        evaluators={
            "output": make_case().evaluators.output.model_dump(exclude_none=True),
            "trajectory": {
                "required_tools_called": {"tools": ["mcp__acme__get_bond_trades"]},
                "tool_trajectory_match": {
                    "expected": [{"name": "get_bond_trades", "args": {"isins": "INE831R07540"}}]
                },
            },
        }
    )
    assert run_lint([case]) == []


def test_overlay_with_only_tool_trajectory_evaluators_lints_clean():
    overlay = Overlay.model_validate(
        {
            "extends": "case-1",
            "agents": ["support-bot"],
            "evaluators": {"trajectory": {"required_tools_called": {"tools": ["get_news"]}}},
        }
    )
    assert run_lint(overlays=[overlay], other={"case-1": "finance"}) == []
