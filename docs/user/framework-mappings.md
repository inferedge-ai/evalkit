# Framework mappings

Ready-to-use [mappings](mappings.md) for popular agent frameworks. Pick your framework, copy its file, and create the agent with it. Each one is the `connect-traces` skill's `openinference.json` or `gen_ai.json` plus a few changes, and ships beside them in the skill's `references/` folder (`evalkit skills install` copies it to `.claude/skills/connect-traces/references/` or `.agents/skills/…`).

```bash
cp .claude/skills/connect-traces/references/<framework>.json eval/span-mapping.json
evalkit agents create myagent --trajectory-source otel --mapping eval/span-mapping.json
```

Then run one session and check it with `evalkit traces check myagent <session id>` (see [Tracing](tracing.md)). Instrumentors change between versions, so read every `!` line even when you start from a file below.

| Framework | Instrumentation | File | Verified with |
| -- | -- | -- | -- |
| LangGraph | OpenInference LangChain | `openinference.json` | LangGraph 1.2 |
| LangChain `create_agent` | OpenInference LangChain | `langchain.json` | LangChain 1.4 |
| CrewAI | OpenInference CrewAI + OpenAI | `crewai.json` | CrewAI 1.15 |
| Agno | OpenInference Agno | `agno.json` | Agno 3.1 |
| OpenAI Agents SDK | OpenInference openai-agents | `openinference.json` | openai-agents 0.24 |
| LlamaIndex `FunctionAgent` | OpenInference LlamaIndex | `llamaindex.json` | LlamaIndex 0.14 |
| Google ADK | ADK's own GenAI spans | `google-adk.json` | ADK 2.13 |
| Pydantic AI | built-in GenAI spans | `gen_ai.json` | Pydantic AI 2.65 |
| Claude Agent SDK | hand-built GenAI spans + the `mcp` library | `claude-agent-sdk.json` | — |
| Mastra (TypeScript) | `@mastra/otel-exporter` GenAI spans | `mastra.json` | Mastra 1.81 |

Some fixes need a change in the agent, not the mapping: a mapping reads one span at a time and cannot move a session id or an output from one span to another. Each section says what the agent still needs.

## Every framework: a session id on each turn

Put the session id on the outermost span of each turn. Each span joins the session of its outermost ancestor that carries one, so a web framework's request span (FastAPI, Starlette) above it that has no session id does not matter. See [Agent behind an HTTP server](tracing.md#agent-behind-an-http-server) for the ways to stamp it.

When the agent runs inside the process that calls `run_experiment`, the SDK's `evalkit.run` span carries the session id and this is already done.

Tool spans from most OpenInference instrumentors carry no `tool.id`. The check then reports "0 matched to calls by id", and evalkit matches tool results to calls by time and order. That is expected; nothing to fix.

## LangGraph

`openinference.json` as it is.

- A graph node named `agent` (the tutorial's default) is emitted as an AGENT span. If every model step shows up as a subagent, rename the node, or add `"rekind_spans": [{"name": "agent", "kind": "chain"}]`.

## LangChain

`langchain.json`: `openinference.json` with `input.value`, `output.value`, `llm.input_messages.*` and `llm.output_messages.*` in `known_attributes`. LangChain's CHAIN spans carry flattened messages that the trajectory does not need.

- Put the session id on a span of your own around each turn (see above).

## CrewAI

`crewai.json`: `openinference.json` with CrewAI's agent and tool attributes (`agent.backstory`, `agent.goal`, `agent.tools`, `conversation.turn_index`, `tool.cache_function`, `tool.current_usage_count`, `tool.description_updated`, `tool.result_as_answer`) in `known_attributes`.

- Pass `provider="openai"` (or your provider) to the LLM, or the OpenAI instrumentor does not see the calls.
- If you wrap each turn in your own span, give it kind CHAIN, not AGENT.

## Agno

`agno.json`: `openinference.json` with `agno.*` in `known_attributes`.

- Set `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT=4096`. The default of 128 attributes per span drops the system prompt from the flattened messages.

## OpenAI Agents SDK

`openinference.json` as it is.

- Wrap each turn in OpenInference's `using_session(session_id)`, so the agent's spans carry the session id.
- The workflow trace is an AGENT span named after `workflow_name`. If the real agent shows up as a subagent of it, add `"exclude_spans": ["<workflow name>"]`.

## LlamaIndex

`llamaindex.json`: `openinference.json` with `input.value` and `output.value` in `known_attributes`, and two `rekind_spans` rules. LlamaIndex emits three LLM-kind spans per model call (an empty `_prepare_chat_with_tools` span and an outer `astream_chat` span around the real one). The rules keep the `astream_chat` span that carries `llm.finish_reason` as the LLM call and make every other `OpenAILike.*` span a chain:

```json
"rekind_spans": [
  {"name": "OpenAILike.astream_chat", "attribute": "llm.finish_reason", "kind": "llm"},
  {"name": "OpenAILike.*", "kind": "chain"}
]
```

- Replace `OpenAILike` with your LLM class's name.
- For token counts on streamed calls, pass `additional_kwargs={"stream_options": {"include_usage": True}}` to the LLM.
- Put the session id on a span of your own around each turn (see above).

## Google ADK

`google-adk.json`: `gen_ai.json` with `"exclude_spans": ["execute_tool (merged)"]` (ADK emits a merged copy of parallel tool calls) and ADK's own attributes (`gcp.vertex.agent.*`, `input.value`, `output.value`, `session.id`) in `known_attributes`.

- Set `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=SPAN_ONLY`. On ADK 2.13, `true` gives spans without messages.
- Put the session id on a span of your own around each turn (see above), or create the app with `FastAPI(telemetry={"tracing": False})`.
- ADK's `execute_tool` spans carry no `gen_ai.conversation.id`; they join the session through the turn span above them.
- ADK reports the model's messages with role `model`, not `assistant`. Until evalkit reads that role, tool calls may be missing from the trajectory.

## Pydantic AI

`gen_ai.json` as it is.

- Pydantic AI sets `gen_ai.conversation.id` to a new id per `agent.run`. Your turn span's session id wins over it (see above); passing `agent.run(..., conversation_id=session_id)` as well is optional and keeps the ids the same in other trace tools.
- If you use Logfire, add the attributes `traces check` lists as unmapped to `known_attributes`.

## Claude Agent SDK

`claude-agent-sdk.json`: `gen_ai.json` with `"exclude_spans": [{"scope": "mcp-python-sdk"}]` and `mcp.*` and `jsonrpc.*` in `known_attributes`. The `mcp` library emits its own `execute_tool` spans for every `tools/call`, which would count each tool call twice. Excluded spans still report their attributes as unmapped, hence the `known_attributes`.

- The SDK has no OTel instrumentation of its own; the agent builds GenAI spans by hand.
- The SDK reports token usage per turn only: put the totals on the turn's last `chat` span.
- Add your own app's attribute prefixes to `known_attributes`.

## Mastra

`mastra.json`: `gen_ai.json` with these changes:

- `exclude_spans` drops Mastra's internal spans by `mastra.span.type`: `processor_run`, `memory_operation` and `model_chunk`.
- `answer` is `invoke_agent <agent name>.text`. Replace `<agent name>` with your agent's name.
- `known_attributes` adds `mastra.*`, `gen_ai.usage.reasoning_tokens` and `gen_ai.tool.call.id`.

Mastra's `chat` spans end without `gen_ai.output.messages`; the text and tool calls sit on the parent `agent_step` span, and a mapping cannot read a parent span. Wrap the exporter in a `SpanExporter` that copies `mastra.model_step.output` from each `agent_step` span onto its `chat` span.

Mastra is TypeScript and the SDK is Python: a Python task calls the agent over HTTP and passes `metadata["session_id"]` along, and the agent uses it as its session id (see [SDK runs](sdk-runs.md)).
