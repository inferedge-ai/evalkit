import { describe, expect, it } from "vitest";
import { messageCounts } from "./esfSummary";
import type { EsfSpan } from "./types";

function span(id: string, extra: Partial<EsfSpan>): EsfSpan {
  return {
    span_id: id,
    trace_id: "t",
    parent_id: null,
    name: id,
    kind: "llm",
    started_at: "2026-10-12T00:00:00Z",
    ended_at: null,
    error: null,
    unmapped: [],
    ...extra,
  };
}

describe("messageCounts", () => {
  it("counts first-call inputs, every output and the tool calls in outputs", () => {
    const esf = [
      span("first", {
        input_messages: [
          { role: "system", text: "s" },
          { role: "user", text: "q" },
        ],
        output_messages: [
          { role: "assistant", tool_calls: [{ id: "c1", name: "search" }] },
        ],
      }),
      span("later", {
        input_messages: [],
        output_messages: [{ role: "assistant", text: "done" }],
      }),
      span("tool", {
        kind: "tool",
        output_messages: [{ role: "assistant", text: "ignored" }],
      }),
    ];
    expect(messageCounts(esf)).toEqual({ input: 2, output: 2, toolCalls: 1 });
    expect(messageCounts([])).toEqual({ input: 0, output: 0, toolCalls: 0 });
  });
});
