from datetime import UTC, datetime, timedelta

from evalkit.contracts.atif import validate_trajectory
from evalkit.contracts.esf import EsfMessage, EsfSpan, EsfToolCall, MappingDocument
from evalkit.core.traces.convert import to_atif
from evalkit.core.trajectory import TrajectoryView

T0 = datetime(2026, 10, 9, tzinfo=UTC)


def span(span_id, kind, *, parent=None, name=None, at=0, end=1, **fields) -> EsfSpan:
    return EsfSpan(
        span_id=span_id,
        trace_id="trace",
        parent_id=parent,
        name=name or span_id,
        kind=kind,
        started_at=T0 + timedelta(seconds=at),
        ended_at=None if end is None else T0 + timedelta(seconds=end),
        error=None,
        **fields,
    )


def llm(span_id, *, parent, at, calls=(), text="", inputs=None, model="m", tokens=(5, 2)):
    tool_calls = [EsfToolCall(id=c, name="lookup", arguments={"q": c}) for c in calls]
    return span(
        span_id,
        "llm",
        parent=parent,
        at=at,
        end=at + 1,
        model=model,
        input_messages=inputs,
        output_messages=[EsfMessage(role="assistant", text=text, tool_calls=tool_calls or None)],
        prompt_tokens=tokens[0],
        completion_tokens=tokens[1],
    )


def tool(span_id, *, parent, at, call_id=None, output="ok"):
    return span(
        span_id,
        "tool",
        parent=parent,
        at=at,
        end=at + 0.5,
        tool_name="lookup",
        tool_call_id=call_id,
        output=output,
    )


CFG = MappingDocument(schema_version="1")


def convert(spans, cfg=CFG):
    doc = to_atif(spans, cfg, agent_name="research-agent", session_id="sess", mapping_version=None)
    validate_trajectory(doc.model_dump(exclude_none=True))
    return doc


def test_tool_results_match_by_id_then_order():
    inputs = [EsfMessage(role="system", text="sys"), EsfMessage(role="user", text="q")]
    spans = [
        span("root", "agent", output="42"),
        llm("l1", parent="root", at=1, calls=["a", "b"], inputs=inputs),
        tool("t_b", parent="root", at=2.1, call_id="b", output="B"),
        tool("t_a", parent="root", at=2.2, output="A"),
        llm("l2", parent="root", at=3, text="42"),
    ]
    doc = convert(spans)
    assert [s.source for s in doc.steps] == ["system", "user", "agent", "agent"]
    step = doc.steps[2]
    assert [c.tool_call_id for c in step.tool_calls] == ["a", "b"]
    results = {r.source_call_id: r.content for r in step.observation.results}
    assert results == {"a": "A", "b": "B"}
    assert doc.steps[3].message == "42" and doc.final_metrics.total_prompt_tokens == 10
    assert doc.agent.name == "research-agent" and doc.agent.model_name == "m"
    assert doc.extra["trajectory_source"] == "otel" and doc.session_id == "sess"


def test_unmatched_tool_span_has_no_source_call_id():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"]),
        tool("t", parent="root", at=2, call_id="zzz", output="X"),
        tool("u", parent="root", at=2.3, output="Y"),
    ]
    step = convert(spans).steps[0]
    ids = [r.source_call_id for r in step.observation.results]
    assert ids == ["a", None]


def test_nested_agent_spans_become_subagents():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1),
        span("sub", "agent", parent="root", at=2, end=4),
        llm("l_sub", parent="sub", at=2.5, text="sub answer"),
        llm("l2", parent="root", at=5, text="done"),
    ]
    doc = convert(spans)
    assert len(doc.steps) == 2 and len(doc.subagent_trajectories) == 1
    assert doc.subagent_trajectories[0].steps[0].message == "sub answer"
    assert doc.subagent_trajectories[0].trajectory_id


def test_sdk_chain_root_with_one_agent_child_is_unwrapped_and_answer_from_root():
    spans = [
        span("sdk", "chain", name="evalkit.run", output="the answer", at=0, end=9),
        span("agent", "agent", parent="sdk", at=0.5, end=8),
        llm("l1", parent="agent", at=1, calls=["a"]),
        tool("t", parent="agent", at=2, call_id="a"),
    ]
    doc = convert(spans)
    assert doc.subagent_trajectories is None
    assert doc.steps[-1].source == "agent" and doc.steps[-1].message == "the answer"


def test_answer_from_named_span_and_key():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"]),
        span("final", "chain", parent="root", at=2, output={"text": "named"}),
    ]
    assert (
        convert(spans, MappingDocument(schema_version="1", answer="final.text")).steps[-1].message
        == "named"
    )
    assert (
        convert(spans, MappingDocument(schema_version="1", answer="final")).steps[-1].message
        == '{"text": "named"}'
    )


def test_excluded_names_and_no_llm_spans():
    spans = [span("root", "agent", output="only"), span("healthcheck", "tool", parent="root", at=1)]
    doc = convert(spans, MappingDocument(schema_version="1", exclude_spans=["healthcheck"]))
    assert [s.source for s in doc.steps] == ["agent"] and doc.steps[0].message == "only"


def test_span_without_end_time_is_noted():
    spans = [span("root", "agent", end=None), llm("l1", parent="root", at=1)]
    doc = to_atif(
        spans,
        CFG,
        agent_name="a",
        session_id=None,
        mapping_version=3,
        incomplete_ended_at=T0 + timedelta(seconds=30),
    )
    assert doc.extra["incomplete_spans"] == 1 and doc.extra["mapping_version"] == 3


def test_multiple_traces_get_a_synthetic_root():
    a = EsfSpan(
        span_id="a",
        trace_id="t1",
        parent_id=None,
        name="a",
        kind="agent",
        started_at=T0,
        ended_at=T0,
        error=None,
    )
    b = llm("b", parent=None, at=1).model_copy(update={"trace_id": "t2"})
    doc = convert([a, b])
    assert len(doc.steps) == 1


def test_render_matches_golden():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"], inputs=[EsfMessage(role="user", text="q")]),
        tool("t", parent="root", at=2, call_id="a", output="R"),
        llm("l2", parent="root", at=3, text="A"),
    ]
    lines = TrajectoryView(convert(spans)).render_steps()
    assert lines == [
        "1. [user] q",
        '2. [tool] lookup  args: {"q": "a"}  result: R',
        "3. [agent] A",
    ]


def test_tool_span_starting_before_its_llm_span_ends_is_kept():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a", "b"]),
        tool("t_a", parent="root", at=1.9995, call_id="a", output="A"),
        tool("t_b", parent="root", at=1.9996, output="B"),
        llm("l2", parent="root", at=3, text="done"),
    ]
    step = convert(spans).steps[0]
    assert {r.source_call_id: r.content for r in step.observation.results} == {"a": "A", "b": "B"}


def test_tool_spans_match_their_call_by_id_across_the_session():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"]),
        llm("l2", parent="root", at=3, calls=["b"]),
        tool("t_a", parent="root", at=3.5, call_id="a", output="A"),
        tool("t_b", parent="root", at=3.6, call_id="b", output="B"),
    ]
    doc = convert(spans)
    assert [r.content for r in doc.steps[0].observation.results] == ["A"]
    assert [r.content for r in doc.steps[1].observation.results] == ["B"]
    assert "unplaced_tool_spans" not in doc.extra


def test_tool_span_in_no_step_is_noted():
    spans = [
        span("root", "agent"),
        tool("early", parent="root", at=0.5),
        llm("l1", parent="root", at=1, text="done"),
    ]
    assert convert(spans).extra["unplaced_tool_spans"] == ["early"]


def test_agent_span_under_a_non_agent_span_is_the_main_agent():
    spans = [
        span("sdk", "chain", name="evalkit.run", output="the answer", at=0, end=9),
        span("http", "other", parent="sdk", at=0.2, end=8.5),
        span("agent", "agent", parent="http", at=0.5, end=8),
        llm("l1", parent="agent", at=1, calls=["a"]),
        tool("t", parent="agent", at=2, call_id="a"),
    ]
    doc = convert(spans)
    assert doc.subagent_trajectories is None
    assert [s.source for s in doc.steps] == ["agent", "agent"]
    assert doc.final_metrics.total_prompt_tokens == 5


def test_worker_trace_beside_the_sdk_root_is_the_main_agent():
    worker = "worker"
    spans = [
        span("sdk", "chain", name="evalkit.run", output="the answer", at=0, end=9),
        span("agent", "agent", at=0.5, end=8, output="worker answer"),
        llm("l1", parent="agent", at=1, calls=["a"]),
        tool("t", parent="agent", at=2, call_id="a"),
        llm("l2", parent="agent", at=3, text="worker answer"),
    ]
    spans = [s if s.span_id == "sdk" else s.model_copy(update={"trace_id": worker}) for s in spans]
    doc = convert(spans)
    assert doc.subagent_trajectories is None
    assert [s.source for s in doc.steps] == ["agent", "agent"]
    assert doc.steps[0].observation.results[0].source_call_id == "a"
    assert doc.steps[-1].message == "worker answer"
    assert doc.trajectory_id == f"{worker}:agent"


def test_root_final_metrics_include_subagent_tokens():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"], tokens=(10, 1)),
        span("sub", "agent", parent="root", at=2, end=4),
        llm("l_sub", parent="sub", at=2.5, text="sub answer", tokens=(100, 20)),
        span("subsub", "agent", parent="sub", at=3, end=3.9),
        llm("l_subsub", parent="subsub", at=3.1, text="deep", tokens=(1000, 300)),
        llm("l2", parent="root", at=5, text="done", tokens=(10, 1)),
    ]
    doc = convert(spans)
    assert doc.final_metrics.total_prompt_tokens == 1120
    assert doc.final_metrics.total_completion_tokens == 322
    assert doc.final_metrics.total_steps == 2
    assert doc.subagent_trajectories[0].final_metrics.total_prompt_tokens == 1100


def test_only_the_outermost_tool_span_is_a_result():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"]),
        tool("outer", parent="root", at=2, call_id="a", output="A"),
        tool("inner", parent="outer", at=2.1, output=None),
        llm("l2", parent="root", at=3, text="done"),
    ]
    results = convert(spans).steps[0].observation.results
    assert [(r.source_call_id, r.content) for r in results] == [("a", "A")]


def test_tool_call_that_spawned_a_subagent_points_at_it():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a", "b"]),
        tool("task", parent="root", at=2, call_id="a", output="from tool"),
        span("sub_a", "agent", parent="task", at=2.1, end=2.4),
        llm("l_a", parent="sub_a", at=2.2, text="sub a"),
        span("sub_b", "agent", parent="root", at=2.5, end=2.9, tool_call_id="b", output="B"),
        llm("l_b", parent="sub_b", at=2.6, text="sub b"),
        llm("l2", parent="root", at=3, text="done"),
    ]
    doc = convert(spans)
    by_call = {r.source_call_id: r for r in doc.steps[0].observation.results}
    assert by_call["a"].content == "from tool"
    assert by_call["a"].subagent_trajectory_ref[0].trajectory_id == "trace:sub_a"
    assert by_call["b"].content == "B"
    assert by_call["b"].subagent_trajectory_ref[0].trajectory_id == "trace:sub_b"
    assert [s.trajectory_id for s in doc.subagent_trajectories] == ["trace:sub_a", "trace:sub_b"]


def test_subagent_without_a_call_id_takes_the_next_pending_call():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"]),
        span("sub", "agent", parent="root", at=2, end=2.5),
        llm("l_sub", parent="sub", at=2.1, text="sub"),
        llm("l2", parent="root", at=3, text="done"),
    ]
    results = convert(spans).steps[0].observation.results
    assert [(r.source_call_id, r.subagent_trajectory_ref[0].trajectory_id) for r in results] == [
        ("a", "trace:sub")
    ]


def test_reasoning_and_cache_writes_reach_the_step():
    call = llm("l1", parent="root", at=1, text="A")
    call.output_messages[0].reasoning = "Think."
    call.cache_write_tokens = 4
    step = convert([span("root", "agent"), call]).steps[0]
    assert step.reasoning_content == "Think."
    assert step.metrics.extra == {"cache_creation_input_tokens": 4}


def test_answer_from_a_gen_ai_agent_root_is_the_last_assistant_text():
    output = [
        {"role": "assistant", "parts": [{"type": "text", "content": "first"}]},
        {
            "role": "assistant",
            "parts": [
                {"type": "reasoning", "content": "hmm"},
                {"type": "text", "content": "The answer"},
                {"type": "text", "content": " is 42."},
            ],
        },
    ]
    spans = [span("root", "agent", output=output), llm("l1", parent="root", at=1, calls=["a"])]
    assert convert(spans).steps[-1].message == "The answer is 42."


def test_subagent_is_named_by_its_agent_name_else_its_span_name():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a", "b"]),
        span(
            "sub_a",
            "agent",
            name="claude_code.tool",
            parent="root",
            at=2,
            end=2.4,
            agent_name="researcher",
        ),
        llm("l_a", parent="sub_a", at=2.1, text="sub a"),
        span("sub_b", "agent", name="Task", parent="root", at=2.5, end=2.9),
        llm("l_b", parent="sub_b", at=2.6, text="sub b"),
    ]
    subs = convert(spans).subagent_trajectories
    assert [s.agent.name for s in subs] == ["researcher", "Task"]


def test_every_span_built_step_has_a_message():
    spans = [
        span("root", "agent"),
        llm("l1", parent="root", at=1, calls=["a"], inputs=[EsfMessage(role="user")]),
        tool("t", parent="root", at=2, call_id="a"),
        llm("l2", parent="root", at=3, text="done"),
    ]
    spans[1].output_messages[0].text = None
    dumped = convert(spans).model_dump(exclude_none=True)
    assert [s["message"] for s in dumped["steps"]] == ["", "", "done"]
