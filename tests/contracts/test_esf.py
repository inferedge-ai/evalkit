import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evalkit.contracts.esf import EsfSpan, MappingDocument

ROOT = Path(__file__).parent.parent.parent


def test_reference_mapping_documents_validate():
    for name in ("openinference", "gen_ai"):
        path = ROOT / "skills" / "connect-traces" / "references" / f"{name}.json"
        doc = MappingDocument.model_validate(json.loads(path.read_text()))
        assert doc.schema_version == "1"
        assert doc.kind is not None and doc.kind.values
        assert doc.session_id is not None
        assert doc.answer == "root" and doc.exclude_spans == []


def test_unknown_rule_key_is_rejected():
    with pytest.raises(ValidationError, match="extra"):
        MappingDocument.model_validate(
            {"schema_version": "1", "kind": {"attribute": "k", "path": "x"}}
        )
    with pytest.raises(ValidationError):
        MappingDocument.model_validate({"schema_version": "1", "colour": {"attribute": "k"}})


def test_null_rule_means_never_read():
    doc = MappingDocument.model_validate({"schema_version": "1", "cost_usd": None})
    assert doc.cost_usd is None and doc.tokens.prompt is None


def test_json_flag_uses_the_json_alias():
    doc = MappingDocument.model_validate(
        {"schema_version": "1", "input": {"attribute": "input.value", "json": True}}
    )
    assert doc.input is not None and doc.input.json_ is True
    assert doc.model_dump(by_alias=True, exclude_none=True)["input"] == {
        "attribute": "input.value",
        "json": True,
    }


def test_esf_span_defaults():
    span = EsfSpan(
        span_id="s",
        trace_id="t",
        parent_id=None,
        name="n",
        kind="other",
        started_at="2026-10-09T00:00:00Z",
        ended_at=None,
        error=None,
    )
    assert span.unmapped == [] and span.input_messages is None
