import type { EsfSpan } from "./types";

export interface MessageCounts {
  input: number;
  output: number;
  toolCalls: number;
}

export function messageCounts(esf: EsfSpan[]): MessageCounts {
  const llm = esf.filter((s) => s.kind === "llm");
  const output = llm.flatMap((s) => s.output_messages ?? []);
  return {
    input: llm.reduce((n, s) => n + (s.input_messages?.length ?? 0), 0),
    output: output.length,
    toolCalls: output.reduce((n, m) => n + (m.tool_calls?.length ?? 0), 0),
  };
}
