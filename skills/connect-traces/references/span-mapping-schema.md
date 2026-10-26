# Span mapping schema (version 1)

A mapping is one JSON document with one rule per ESF field. Every mapping is
complete on its own; there is no inheritance. The JSON Schema ships as
`span-mapping-v1.json` with `evalkit contracts export`.

```json
{
  "schema_version": "1",
  "kind": {"attribute": "...", "values": {"<raw>": "llm|tool|agent|chain|retriever|other"}},
  "session_id": {"attribute": "..."},
  "model": {"attribute": "..."},
  "system_instructions": {"attribute": "...", "json": true},
  "input_messages": {"attribute": "...", "message_format": "openinference|gen_ai|openai"},
  "output_messages": {"attribute": "...", "message_format": "openinference|gen_ai|openai"},
  "tokens": {
    "prompt": {"attribute": "..."},
    "completion": {"attribute": "..."},
    "cached": {"attribute": "..."},
    "cache_write": {"attribute": "..."}
  },
  "cost_usd": {"attribute": "..."},
  "tool": {
    "name": {"attribute": "..."},
    "call_id": {"attribute": "..."},
    "arguments": {"attribute": "...", "json": true},
    "result": {"attribute": "..."}
  },
  "agent_name": {"attribute": "..."},
  "input": {"attribute": "...", "json": true},
  "output": {"attribute": "...", "json": true},
  "known_attributes": ["...", "prefix.*"],
  "answer": "root",
  "exclude_spans": ["..."]
}
```

Two fields apply to the whole run, not to one span:

- `answer` (default `root`): where the run's answer comes from. `root`, a
  span name, or `<span name>.<output key>`.
- `exclude_spans` (default empty): span names left out of the trajectory.

## Rule shapes

| Shape | What evalkit does |
| -- | -- |
| `{"attribute": K}` | reads attribute `K`; null when absent |
| `{"attribute": K, "json": true}` | reads `K` and parses it with `json.loads` when it is a string; a parse failure keeps the string |
| `{"attribute": K, "values": {...}}` | reads `K` and translates the value through the table; for `kind`, an unlisted value becomes `other` |
| `{"attribute": K, "message_format": F}` | collects the messages laid out per format `F` under `K` (see below) |
| `null` | the field is never read |

Which fields are read depends on the kind: `system_instructions`,
`input_messages`, `output_messages` and the token rules on `llm` spans; the `tool` rules on
`tool` spans, and `tool.call_id` on `agent` spans too (the call that started
a subagent); `agent_name` on `agent` spans (it names the subagent's
trajectory; the span name is used without it); `input` and `output` on everything that is not an llm span.
Attributes no rule read are reported as unmapped by `evalkit traces check`,
except those matching an entry of `known_attributes` (exact keys or glob
patterns such as `llm.tools.*`): list there the attributes you know about and
do not need.

`system_instructions` holds a system prompt kept outside the input messages,
as a string or a list of `{"type": "text", "content"}` parts; it becomes the
first input message, with role `system`. `tokens.cache_write` counts input
tokens written to the prompt cache; it lands in the step's
`metrics.extra.cache_creation_input_tokens`.

## Message formats

- `openinference`: flattened indexed keys under the attribute prefix `K`:
  `K.{i}.message.role`, `K.{i}.message.content`,
  `K.{i}.message.tool_calls.{j}.tool_call.id`,
  `K.{i}.message.tool_calls.{j}.tool_call.function.name`,
  `K.{i}.message.tool_calls.{j}.tool_call.function.arguments`,
  `K.{i}.message.tool_call_id`, and content parts
  `K.{i}.message.contents.{j}.message_content.type` / `.text`, where type
  `text` is message text (when `message.content` is absent) and type
  `reasoning` is the model's reasoning.
- `gen_ai`: attribute `K` holds a JSON string: a list of messages with `role`
  and `parts`, each part typed `text` (`content`), `reasoning` (`content`),
  `tool_call` (`id`, `name`, `arguments`) or `tool_call_response` (`id`,
  `response`, or `result` when `response` is absent).
- `openai`: attribute `K` holds a JSON string: a list of OpenAI chat messages
  with `role`, `content` (a string or a list of `{"type": "text", "text"}`
  parts), `tool_calls` (`id`, `function.name`, `function.arguments` as a JSON
  string) and `tool_call_id`.
