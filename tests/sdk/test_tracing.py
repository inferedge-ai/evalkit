import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from evalkit.sdk import tracing


@contextmanager
def collector() -> Iterator[tuple[str, list[dict[str, str]]]]:
    received: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def one_span() -> list:
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    provider.get_tracer("t").start_span("s").end()
    return list(memory.get_finished_spans())


def test_stamping_processor_copies_only_the_four_attributes():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    tracing.configure(provider, exporter)
    tracer = provider.get_tracer("t")
    token = tracing.CURRENT_RUN.set(
        {
            "session.id": "r1",
            "evalkit.experiment_id": "e",
            "evalkit.case_id": "c",
            "evalkit.repetition": 2,
            "evalkit.sdk_root": True,
        }
    )
    try:
        with tracer.start_as_current_span("root", attributes={"evalkit.sdk_root": True}):
            with tracer.start_as_current_span("child"):
                pass
    finally:
        tracing.CURRENT_RUN.reset(token)
    provider.force_flush()
    spans = {s.name: dict(s.attributes) for s in exporter.get_finished_spans()}
    assert spans["child"] == {
        "session.id": "r1",
        "evalkit.experiment_id": "e",
        "evalkit.case_id": "c",
        "evalkit.repetition": 2,
    }
    assert spans["root"]["evalkit.sdk_root"] is True


def test_collector_url_and_token_resolution(monkeypatch):
    assert tracing.collector_url("http://srv/") == "http://srv/api/otel/v1/traces"
    monkeypatch.setenv("EVALKIT_OTEL_TOKEN", "agent-token")
    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://env")
    url, token = tracing.resolve_destination()
    assert (url, token) == ("http://env", "agent-token")


def test_register_adds_an_exporter_to_the_global_provider(monkeypatch):
    with collector() as (url, received):
        monkeypatch.setenv("EVALKIT_SERVER_URL", url)
        monkeypatch.setenv("EVALKIT_TOKEN", "user-token")
        monkeypatch.delenv("EVALKIT_OTEL_TOKEN", raising=False)
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
        tracing.register(agent="sdk")
        processors = provider._active_span_processor._span_processors
        evalkit = next(p for p in processors if isinstance(p, tracing.EvalkitSpanProcessor))
        evalkit.batch.span_exporter.export(one_span())
    [request] = received
    assert request["path"] == "/api/otel/v1/traces"
    assert request["authorization"] == "Bearer user-token"
    assert request["x-evalkit-agent"] == "sdk"


def test_exporter_prefers_the_agent_token_from_the_environment(monkeypatch):
    monkeypatch.setenv("EVALKIT_TOKEN", "user-token")
    monkeypatch.setenv("EVALKIT_OTEL_TOKEN", "agent-token")
    with collector() as (url, received):
        monkeypatch.setenv("EVALKIT_SERVER_URL", url)
        tracing.exporter(agent="worker@akshat").export(one_span())
    [request] = received
    assert request["path"] == "/api/otel/v1/traces"
    assert request["authorization"] == "Bearer agent-token"
    assert request["x-evalkit-agent"] == "worker@akshat"
    assert tracing.headers(agent="worker") == {
        "Authorization": "Bearer agent-token",
        "X-Evalkit-Agent": "worker",
    }


def test_processor_fans_in_spans_handed_straight_to_it(monkeypatch):
    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://srv")
    monkeypatch.setenv("EVALKIT_OTEL_TOKEN", "agent-token")
    processor = tracing.processor(agent="bridge")
    exported = InMemorySpanExporter()
    processor.batch = SimpleSpanProcessor(exported)
    source = TracerProvider()
    finished = InMemorySpanExporter()
    source.add_span_processor(SimpleSpanProcessor(finished))
    with source.get_tracer("t").start_as_current_span("converted"):
        pass
    for span in finished.get_finished_spans():
        processor.on_end(span)
    assert [s.name for s in exported.get_finished_spans()] == ["converted"]


def test_processor_filter_exports_only_spans_of_eval_runs():
    exported = InMemorySpanExporter()
    provider = TracerProvider()
    tracing.configure(provider, exported, only=tracing.is_eval_span)
    tracer = provider.get_tracer("worker")
    metadata = {
        "session_id": "s1",
        "experiment_id": None,
        "case_id": "case-1",
        "repetition": 1,
        "tags": [],
    }
    with tracer.start_as_current_span("prod_turn", context=Context()):
        with tracer.start_as_current_span("prod_llm"):
            pass
    with tracing.eval_run(metadata):
        with tracer.start_as_current_span("eval_turn", context=Context()):
            with tracer.start_as_current_span("eval_llm"):
                pass
    with tracer.start_as_current_span("prod_after", context=Context()):
        pass
    provider.force_flush()
    spans = {s.name: dict(s.attributes) for s in exported.get_finished_spans()}
    assert set(spans) == {"eval_turn", "eval_llm"}
    assert spans["eval_llm"] == {
        "session.id": "s1",
        "evalkit.case_id": "case-1",
        "evalkit.repetition": 1,
    }


def eval_only_pipeline():
    exported = InMemorySpanExporter()
    processor = tracing.EvalkitSpanProcessor(exported, only=tracing.is_eval_span)
    processor.batch = SimpleSpanProcessor(exported)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    return provider.get_tracer("worker"), processor, exported


def finished_spans_in(parent: trace.Span, *names: str, attributes: dict | None = None):
    bridge_source = TracerProvider()
    finished = InMemorySpanExporter()
    bridge_source.add_span_processor(SimpleSpanProcessor(finished))
    tracer = bridge_source.get_tracer("bridge")
    for name in names:
        with tracer.start_as_current_span(
            name, context=trace.set_span_in_context(parent), attributes=attributes
        ):
            pass
    return finished.get_finished_spans()


EVAL_METADATA = {"session_id": "s1", "experiment_id": "e", "case_id": "case-1", "repetition": 1}


def test_eval_only_export_keeps_bridged_spans_of_an_eval_trace():
    tracer, processor, exported = eval_only_pipeline()
    with tracing.eval_run(EVAL_METADATA):
        with tracer.start_as_current_span("chat_turn", context=Context()) as turn:
            bridged = finished_spans_in(turn, "llm", "tool")
    for span in bridged:
        processor.on_end(span)
    assert [s.name for s in exported.get_finished_spans()] == ["chat_turn", "llm", "tool"]


def test_eval_only_export_keeps_spans_ending_before_or_after_the_marked_span():
    tracer, processor, exported = eval_only_pipeline()
    with tracer.start_as_current_span("job", context=Context()) as job:
        with tracing.eval_run(EVAL_METADATA):
            with tracer.start_as_current_span("chat_turn"):
                early = finished_spans_in(job, "early_llm")
                for span in early:
                    processor.on_end(span)
        late = finished_spans_in(job, "late_tool")
    for span in late:
        processor.on_end(span)
    assert [s.name for s in exported.get_finished_spans()] == [
        "early_llm",
        "chat_turn",
        "job",
        "late_tool",
    ]


def test_eval_only_export_keeps_a_trace_marked_by_a_bridged_span():
    tracer, processor, exported = eval_only_pipeline()
    with tracer.start_as_current_span("chat_turn", context=Context()) as turn:
        bridged = finished_spans_in(turn, "llm", attributes={"evalkit.case_id": "case-1"})
        processor.on_end(bridged[0])
    assert [s.name for s in exported.get_finished_spans()] == ["llm", "chat_turn"]


def test_eval_only_export_drops_traces_without_an_eval_span():
    tracer, processor, exported = eval_only_pipeline()
    with tracer.start_as_current_span("prod_turn", context=Context()) as turn:
        bridged = finished_spans_in(turn, "prod_llm")
    for span in bridged:
        processor.on_end(span)
    with tracing.eval_run(EVAL_METADATA):
        with tracer.start_as_current_span("eval_turn", context=Context()):
            pass
    assert [s.name for s in exported.get_finished_spans()] == ["eval_turn"]
    assert processor.kept_traces.keys() == {
        exported.get_finished_spans()[0].get_span_context().trace_id
    }


def test_eval_only_export_forgets_the_least_recently_used_trace():
    tracer, processor, exported = eval_only_pipeline()
    processor.max_traces = 2
    turns = []
    for _ in range(3):
        with tracing.eval_run(EVAL_METADATA):
            with tracer.start_as_current_span("eval_turn", context=Context()) as turn:
                turns.append(turn)
    assert len(processor.kept_traces) == 2
    oldest, *recent = turns
    for parent in turns:
        for span in finished_spans_in(parent, "late"):
            processor.on_end(span)
    late_traces = [s.context.trace_id for s in exported.get_finished_spans() if s.name == "late"]
    assert late_traces == [t.get_span_context().trace_id for t in recent]
