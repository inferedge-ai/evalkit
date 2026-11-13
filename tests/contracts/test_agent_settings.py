import pytest
from pydantic import ValidationError

from evalkit.contracts.settings import AgentSettings


@pytest.mark.parametrize("fields", [{"trace_source": "phx"}, {"project": "ra"}])
def test_trace_source_and_project_go_together(fields):
    with pytest.raises(ValidationError, match="trace_source and project go together"):
        AgentSettings(**fields)


def test_trace_source_defaults_and_wait():
    plain = AgentSettings()
    assert plain.trace_source is None and plain.project is None
    assert plain.import_settle_seconds == 15 and plain.import_wait_seconds == 120
    assert plain.wait_seconds() == 60
    assert AgentSettings(trace_source="phx", project="ra").wait_seconds() == 120
