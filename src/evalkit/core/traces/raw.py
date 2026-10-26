from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RawSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: str = "INTERNAL"
    start_time: datetime
    end_time: datetime | None = None
    status_code: str = "UNSET"
    status_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    resource: dict[str, Any] = Field(default_factory=dict)
    received_at: datetime | None = None
    session_id_hint: str | None = Field(default=None, exclude=True)
