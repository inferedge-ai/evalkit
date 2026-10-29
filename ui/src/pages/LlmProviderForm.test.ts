import { describe, expect, it } from "vitest";
import { providerBody } from "./LlmProviderForm";
import type { LlmProviderInfo } from "../lib/types";

const current: LlmProviderInfo = {
  name: "openai",
  base_url: "https://api.openai.com/v1",
  has_api_key: true,
  archived: false,
};
const none = { key: "", clear: false };

describe("LLM provider form body", () => {
  it("creates with name, base URL and an optional key", () => {
    expect(providerBody(" openai ", " https://o/v1 ", none)).toEqual({
      name: "openai",
      base_url: "https://o/v1",
    });
    expect(
      providerBody("o", "https://o/v1", { key: "sk", clear: false }).api_key,
    ).toBe("sk");
  });

  it("edits only what changed", () => {
    expect(providerBody("", current.base_url, none, current)).toEqual({});
    expect(providerBody("", "https://moved/v1", none, current)).toEqual({
      base_url: "https://moved/v1",
    });
    expect(
      providerBody("", current.base_url, { key: "", clear: true }, current),
    ).toEqual({ api_key: null });
  });
});
