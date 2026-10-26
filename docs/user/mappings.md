# Mappings

A mapping tells evalkit which span attribute holds each field of the evalkit span format (ESF): the span kind, the session id, the model, the messages, the token counts, the tool name and call id. It also says where the run's answer comes from (`answer`) and which spans to leave out (`exclude_spans`).

Every agent whose trajectories come from traces has its own mapping. `evalkit agents create NAME --trajectory-source otel --mapping FILE` makes the first version active. Start from the `connect-traces` skill's `openinference.json` or `gen_ai.json`, which cover agents on the OpenInference or GenAI semantic conventions as they are. Spans that reach an agent with no active mapping are stored without a session id.

An existing agent switches between ATIF and traces with `evalkit agents edit NAME --trajectory-source otel --mapping FILE` (or `--trajectory-source atif`), or from the agent's Edit dialog. Switching to traces without a file reuses the agent's last active mapping. The switch is refused while one of the agent's experiments is running, and experiments that already ran keep the source they ran with.

A mapping applies to every span of the agent and is versioned: `evalkit traces mapping push` creates a draft, you review the draft against recent sessions on the agent's Mappings tab (Agents → the agent → Mappings), and activate it there. The `connect-traces` skill walks a coding agent through writing one.

## The document

```json
{
  "schema_version": "1",
  "kind": {
    "attribute": "openinference.span.kind",
    "values": {
      "LLM": "llm",
      "TOOL": "tool",
      "AGENT": "agent",
      "CHAIN": "chain",
      "RETRIEVER": "retriever"
    }
  },
  "session_id": {
    "attribute": "session.id"
  },
  "model": {
    "attribute": "llm.model_name"
  },
  "input_messages": {
    "attribute": "llm.input_messages",
    "message_format": "openinference"
  },
  "output_messages": {
    "attribute": "llm.output_messages",
    "message_format": "openinference"
  },
  "tokens": {
    "prompt": {
      "attribute": "llm.token_count.prompt"
    },
    "completion": {
      "attribute": "llm.token_count.completion"
    },
    "cached": {
      "attribute": "llm.token_count.prompt_details.cache_read"
    },
    "cache_write": {
      "attribute": "llm.token_count.prompt_details.cache_write"
    }
  },
  "cost_usd": {
    "attribute": "llm.cost.total"
  },
  "tool": {
    "name": {
      "attribute": "tool.name"
    },
    "call_id": {
      "attribute": "tool.id"
    },
    "arguments": {
      "attribute": "input.value",
      "json": true
    },
    "result": {
      "attribute": "output.value"
    }
  },
  "agent_name": {
    "attribute": "agent.name"
  },
  "input": {
    "attribute": "input.value",
    "json": true
  },
  "output": {
    "attribute": "output.value",
    "json": true
  },
  "known_attributes": [
    "openinference.span.kind",
    "session.id",
    "user.id",
    "metadata",
    "tag.tags",
    "input.mime_type",
    "output.mime_type",
    "llm.provider",
    "llm.system",
    "llm.invocation_parameters",
    "llm.finish_reason",
    "llm.tools.*",
    "llm.token_count.total",
    "llm.token_count.prompt_details.*",
    "llm.token_count.completion_details.*",
    "llm.cost.*",
    "tool.description",
    "tool.parameters",
    "tool.json_schema",
    "agent.name",
    "graph.node.*"
  ]
}
```

That is the skill's `openinference.json`. Its `gen_ai.json` reads `gen_ai.operation.name` (`chat`, `execute_tool`, `invoke_agent`, …), `gen_ai.request.model`, `gen_ai.input.messages` / `gen_ai.output.messages` with `"message_format": "gen_ai"`, `gen_ai.usage.input_tokens` and friends, reads the system prompt from `gen_ai.system_instructions`, cache writes from `gen_ai.usage.cache_creation.input_tokens` and the agent's name from `gen_ai.agent.name`, and sets `"cost_usd": null` because the conventions define no cost attribute. Both list the other attributes of their convention under `known_attributes`, so `evalkit traces check` does not report them as unmapped; entries may be exact keys or globs such as `llm.tools.*`.

Rule shapes:

| Shape                                   | Result                                                                                                                                                                       |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `{"attribute": K}`                      | the attribute's value, or null                                                                                                                                               |
| `{"attribute": K, "json": true}`        | the value parsed as JSON when it is a string; the string stays when parsing fails                                                                                            |
| `{"attribute": K, "values": {...}}`     | the value translated through the table; an unlisted kind becomes `other`                                                                                                     |
| `{"attribute": K, "message_format": F}` | the messages laid out under `K` per format `F`: `openinference` (flattened indexed keys), `gen_ai` (JSON list of messages with parts), `openai` (JSON list of chat messages) |
| `null`                                  | never read                                                                                                                                                                   |

Two more fields shape the trajectory: `answer` (`root`, a span name, or `<span name>.<output key>`; default `root`) and `exclude_spans` (span names left out; default none).

Every mapping is complete on its own; there is no inheritance. The schema ships as `span-mapping-v1.json` with `evalkit contracts export`.

## The loop

```bash
evalkit traces check myagent <session id> --dump spans.json   # read the raw attributes
evalkit traces mapping push myagent eval/span-mapping.json    # upload a draft
evalkit traces check myagent <session id> --mapping draft     # preview with the draft
evalkit traces mapping list myagent
```

Repeat until the unmapped list holds nothing that matters and the counts look right, then activate the draft on the agent's Mappings tab. `--activate` on push activates in the same step; a coding agent passes it only when the developer has approved the document.

Runs record the mapping version they were converted with; a later mapping change never alters a scored run. The Runs page converts sessions on demand with the active mapping.
