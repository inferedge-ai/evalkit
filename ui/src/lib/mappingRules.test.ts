import { describe, expect, it } from "vitest";
import { mappingRows, ruleNotes, sameRule } from "./mappingRules";

const DOC = {
  schema_version: "1",
  kind: {
    attribute: "openinference.span.kind",
    values: { LLM: "llm", TOOL: "tool" },
  },
  input_messages: {
    attribute: "llm.input_messages",
    message_format: "openinference",
  },
  tokens: { prompt: { attribute: "llm.token_count.prompt" } },
  cost_usd: null,
  tool: { arguments: { attribute: "input.value", json: true } },
};

describe("mappingRows", () => {
  it("lists every field, reading nested rules and leaving the rest empty", () => {
    const rows = mappingRows(DOC);
    expect(rows).toHaveLength(15);
    const byField = Object.fromEntries(rows.map((r) => [r.field, r.rule]));
    expect(byField["kind"]?.values).toEqual({ LLM: "llm", TOOL: "tool" });
    expect(byField["tokens.prompt"]?.attribute).toBe("llm.token_count.prompt");
    expect(byField["tool.arguments"]?.json).toBe(true);
    expect(byField["cost_usd"]).toBeNull();
    expect(byField["model"]).toBeNull();
  });

  it("describes how a value is read", () => {
    expect(
      ruleNotes({ attribute: "a", message_format: "gen_ai", json: true }),
    ).toEqual(["gen_ai messages", "parsed as JSON"]);
    expect(ruleNotes({ attribute: "a" })).toEqual([]);
  });

  it("compares rules by value", () => {
    expect(sameRule({ attribute: "a" }, { attribute: "a" })).toBe(true);
    expect(sameRule({ attribute: "a" }, { attribute: "b" })).toBe(false);
    expect(sameRule(null, null)).toBe(true);
    expect(sameRule({ attribute: "a" }, null)).toBe(false);
    expect(
      sameRule(
        { attribute: "a", json: false, values: { X: "x", Y: "y" } },
        { values: { Y: "y", X: "x" }, attribute: "a" },
      ),
    ).toBe(true);
  });
});
