from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from importlib.util import find_spec
from typing import Any

from opentelemetry import trace
from opentelemetry.context import Context

from evalkit.client import connection

OTEL_TOKEN_ENV = "EVALKIT_OTEL_TOKEN"
SERVER_URL_ENV = "EVALKIT_SERVER_URL"
AGENT_HEADER = "X-Evalkit-Agent"
STAMPED = ("session.id", "evalkit.experiment_id", "evalkit.case_id", "evalkit.repetition")
EVAL_MARKER = "evalkit.case_id"
MAX_EVAL_TRACES = 10_000
INSTALL_HINT = "evalkit tracing needs the otel extra: uv add 'evalkit-py[otel]'"

CURRENT_RUN: ContextVar[dict[str, Any] | None] = ContextVar("evalkit_current_run", default=None)

SpanFilter = Callable[[Any], bool]

HAS_OTEL_SDK = bool(
    find_spec("opentelemetry.sdk") and find_spec("opentelemetry.exporter.otlp.proto.http")
)

if HAS_OTEL_SDK:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

    class EvalkitSpanProcessor(SpanProcessor):
        def __init__(self, exporter: SpanExporter, only: SpanFilter | None = None) -> None:
            self.batch = BatchSpanProcessor(exporter)
            self.only = only
            self.max_traces = MAX_EVAL_TRACES
            self.kept_traces: OrderedDict[int, None] = OrderedDict()
            self.lock = threading.Lock()

        def on_start(self, span: Any, parent_context: Context | None = None) -> None:
            attributes = CURRENT_RUN.get()
            if attributes:
                for key in STAMPED:
                    if key in attributes:
                        span.set_attribute(key, attributes[key])
            if self.only is not None and self.only(span):
                self.keep_trace(trace_of(span))

        def on_end(self, span: ReadableSpan) -> None:
            if self.only is None:
                self.batch.on_end(span)
                return
            trace_id = trace_of(span)
            if self.only(span):
                self.keep_trace(trace_id)
            if self.is_kept(trace_id):
                self.batch.on_end(span)

        def is_kept(self, trace_id: int) -> bool:
            with self.lock:
                if trace_id not in self.kept_traces:
                    return False
                self.kept_traces.move_to_end(trace_id)
                return True

        def keep_trace(self, trace_id: int) -> None:
            with self.lock:
                self.kept_traces[trace_id] = None
                self.kept_traces.move_to_end(trace_id)
                while len(self.kept_traces) > self.max_traces:
                    self.kept_traces.popitem(last=False)

        def shutdown(self) -> None:
            self.batch.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return self.batch.force_flush(timeout_millis)

    def configure(
        provider: TracerProvider, exporter: SpanExporter, only: SpanFilter | None = None
    ) -> None:
        provider.add_span_processor(EvalkitSpanProcessor(exporter, only))


def trace_of(span: Any) -> int:
    return span.get_span_context().trace_id


def collector_url(server_url: str) -> str:
    return f"{server_url.rstrip('/')}/api/otel/v1/traces"


def resolve_destination() -> tuple[str, str | None]:
    token = os.environ.get(OTEL_TOKEN_ENV)
    url = os.environ.get(SERVER_URL_ENV)
    if url and token:
        return url, token
    active = connection.active()
    return url or active.url, token or active.token


def headers(agent: str) -> dict[str, str]:
    _, token = resolve_destination()
    out = {AGENT_HEADER: agent}
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out


def exporter(agent: str) -> OTLPSpanExporter:
    if not HAS_OTEL_SDK:
        raise RuntimeError(INSTALL_HINT)
    url, _ = resolve_destination()
    return OTLPSpanExporter(endpoint=collector_url(url), headers=headers(agent))


def processor(agent: str, only: SpanFilter | None = None) -> EvalkitSpanProcessor:
    return EvalkitSpanProcessor(exporter(agent), only)


def is_eval_span(span: Any) -> bool:
    return EVAL_MARKER in (span.attributes or {})


@contextmanager
def eval_run(metadata: dict[str, Any]) -> Iterator[None]:
    attributes = {
        "session.id": metadata.get("session_id"),
        "evalkit.experiment_id": metadata.get("experiment_id"),
        "evalkit.case_id": metadata.get("case_id"),
        "evalkit.repetition": metadata.get("repetition"),
    }
    token = CURRENT_RUN.set({k: v for k, v in attributes.items() if v is not None})
    try:
        yield
    finally:
        CURRENT_RUN.reset(token)


def register(agent: str, only: SpanFilter | None = None) -> None:
    """Send this process's OpenTelemetry spans to the evalkit server.

    Adds an exporter to the global tracer provider, or creates one when none is set.
    Call it after your own tracing setup, so it joins your provider. Spans go to the
    server you logged in to (or `EVALKIT_SERVER_URL`), with your token or
    `EVALKIT_OTEL_TOKEN` (an agent token). Needs the `otel` extra.

    Args:
        agent: The agent the spans belong to, e.g. `myagent` or `myagent@dev`.
        only: Export only the spans this function returns `True` for.
    """
    span_processor = processor(agent, only)
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        provider = current
    else:
        provider = TracerProvider(resource=Resource.create({"evalkit.agent": agent}))
        trace.set_tracer_provider(provider)
    provider.add_span_processor(span_processor)
