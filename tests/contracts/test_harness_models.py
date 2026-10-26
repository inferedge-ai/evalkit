import pytest
from pydantic import ValidationError

from evalkit.contracts.harness import (
    DEFAULT_INPUT_SCHEMA,
    Accepts,
    Capabilities,
    FileRef,
    RunRequest,
    RunStatus,
    answer_text,
    input_errors,
)


def test_accepts_file_input_is_either_flag():
    assert Accepts().file_input is False
    assert Accepts(files=True).file_input is True
    assert Accepts(file_urls=True).file_input is True


def test_run_request_carries_file_refs():
    req = RunRequest(
        input={"question": "q"}, files=[FileRef(name="a.csv", url="s3://b/fixtures/a.csv")]
    )
    assert req.model_dump(exclude_none=True)["files"] == [
        {"name": "a.csv", "url": "s3://b/fixtures/a.csv"}
    ]


SCHEMA = {
    "type": "object",
    "required": ["question", "source_type"],
    "properties": {"question": {"type": "string"}, "source_type": {"type": "string"}},
}


def test_completed_run_may_carry_only_output():
    status = RunStatus(status="completed", model="m", output={"label": "EVENT"})
    assert status.answer is None and status.output == {"label": "EVENT"}


def test_completed_run_needs_answer_or_output():
    with pytest.raises(ValidationError, match="an answer or an output"):
        RunStatus(status="completed", model="m")


def test_input_errors_report_path_and_message():
    assert input_errors(SCHEMA, {"question": "q", "source_type": "news"}) == []
    assert input_errors(SCHEMA, {"question": 1}) == [
        "<root>: 'source_type' is a required property",
        "question: 1 is not of type 'string'",
    ]


def test_capabilities_reject_invalid_input_schema():
    with pytest.raises(ValidationError, match="input_schema"):
        Capabilities(agent="a", input_schema={"type": "nope"})


def test_capabilities_reject_check_input_failing_schema():
    with pytest.raises(ValidationError, match="check_input fails input_schema"):
        Capabilities(agent="a", input_schema=SCHEMA, check_input={"question": "q"})


def test_capabilities_accept_valid_check_input():
    caps = Capabilities(
        agent="a", input_schema=SCHEMA, check_input={"question": "q", "source_type": "news"}
    )
    assert caps.check_input == {"question": "q", "source_type": "news"}


def test_answer_text_prefers_answer_then_canonical_json():
    assert answer_text("hi", {"b": 1}) == "hi"
    assert answer_text(None, {"b": 1, "a": "é"}) == '{"a": "é", "b": 1}'


def test_effective_input_schema_defaults_to_question():
    assert Capabilities(agent="a").effective_input_schema == DEFAULT_INPUT_SCHEMA
    assert Capabilities(agent="a", input_schema=SCHEMA).effective_input_schema == SCHEMA


def test_check_input_must_pass_default_schema():
    with pytest.raises(ValidationError, match="check_input fails input_schema"):
        Capabilities(agent="a", check_input={"text": "doc"})
    assert Capabilities(agent="a", check_input={"question": "q"}).check_input == {"question": "q"}
