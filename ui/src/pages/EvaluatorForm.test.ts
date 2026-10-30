import { describe, expect, it } from "vitest";
import { evaluatorBody, evaluatorFormErrors } from "./EvaluatorForm";
import type { RemoteEvaluatorInfo } from "../lib/types";

const values = {
  name: " rtc ",
  kind: "score" as const,
  endpoint: " https://e/rtc ",
  timeout: "120",
  apiKey: { key: "", clear: false },
};
const current: RemoteEvaluatorInfo = {
  name: "rtc",
  workspace: "dev",
  ref: "rtc@dev",
  kind: "score",
  settings: { endpoint: "https://e/rtc", timeout_seconds: 120 },
  archived: false,
  has_api_key: true,
};

describe("evaluator form", () => {
  it("needs a name, an endpoint and a whole-number timeout", () => {
    expect(evaluatorFormErrors(values, true)).toEqual([]);
    expect(
      evaluatorFormErrors(
        { ...values, name: "", endpoint: " ", timeout: "0" },
        true,
      ),
    ).toEqual([
      "Name is required.",
      "Endpoint is required.",
      "Timeout must be a whole number of at least 1.",
    ]);
    expect(evaluatorFormErrors({ ...values, name: "" }, false)).toEqual([]);
  });

  it("creates with workspace, name, kind, settings and an optional key", () => {
    expect(evaluatorBody(values, "dev")).toEqual({
      workspace: "dev",
      name: "rtc",
      kind: "score",
      settings: { endpoint: "https://e/rtc", timeout_seconds: 120 },
    });
    const keyed = { ...values, apiKey: { key: "k", clear: false } };
    expect(evaluatorBody(keyed, "dev").api_key).toBe("k");
  });

  it("edits settings and sends a key change only when asked", () => {
    expect(evaluatorBody(values, "dev", current)).toEqual({
      settings: { endpoint: "https://e/rtc", timeout_seconds: 120 },
    });
    const cleared = { ...values, apiKey: { key: "", clear: true } };
    expect(evaluatorBody(cleared, "dev", current).api_key).toBeNull();
  });
});
