import { describe, expect, it } from "vitest";
import {
  agentFormErrors,
  agentFormFrom,
  createAgentBody,
  editAgentBody,
  emptyAgentForm,
} from "./agentForm";
import type { AgentInfo } from "./types";

describe("agent form", () => {
  it("needs a name without @", () => {
    expect(agentFormErrors(emptyAgentForm("dev"), "create")).toContain(
      "Name is required.",
    );
    const bad = { ...emptyAgentForm("dev"), name: "a@b" };
    expect(agentFormErrors(bad, "create")).toContain("Name cannot contain @.");
  });

  it("requires a JSON span mapping when trajectories come from traces", () => {
    const otel = {
      ...emptyAgentForm("dev"),
      name: "traced",
      trajectory_source: "otel" as const,
    };
    expect(agentFormErrors(otel, "create")[0]).toMatch(/needs a span mapping/);
    expect(agentFormErrors({ ...otel, mapping: "[1]" }, "create")).toEqual([
      "The span mapping is not a JSON object.",
    ]);
    const ok = { ...otel, mapping: '{"schema_version": "1"}' };
    expect(agentFormErrors(ok, "create")).toEqual([]);
    expect(createAgentBody(ok).mapping).toEqual({ schema_version: "1" });
  });

  it("rejects settings that are not whole numbers", () => {
    const values = { ...emptyAgentForm("dev"), name: "a" };
    values.settings.max_concurrent_runs = "2.5";
    expect(agentFormErrors(values, "create")).toEqual([
      "Max concurrent runs must be a whole number of at least 1.",
    ]);
  });

  it("rejects empty settings and zero concurrent runs", () => {
    const values = { ...emptyAgentForm("dev"), name: "a" };
    values.settings.max_concurrent_runs = "0";
    values.settings.run_timeout_seconds = " ";
    expect(agentFormErrors(values, "create")).toEqual([
      "Max concurrent runs must be a whole number of at least 1.",
      "Run timeout (s) must be a whole number of at least 0.",
    ]);
  });

  it("builds create and edit bodies", () => {
    const values = {
      ...emptyAgentForm("dev"),
      name: " ouragent ",
      harness_url: "http://h",
    };
    expect(createAgentBody(values)).toMatchObject({
      workspace: "dev",
      name: "ouragent",
      harness_url: "http://h",
      mapping: null,
      settings: { max_concurrent_runs: 4 },
    });
    const current = {
      settings: { provides: { thinking: true }, max_concurrent_runs: 4 },
    } as unknown as AgentInfo;
    expect(
      editAgentBody({ ...values, harness_url: "" }, current),
    ).toMatchObject({
      harness_url: null,
      settings: { provides: { thinking: true }, max_concurrent_runs: 4 },
    });
  });

  it("sends a trajectory source switch only when it changes", () => {
    const atif = {
      trajectory_source: "atif",
      settings: { max_concurrent_runs: 4 },
    } as unknown as AgentInfo;
    const values = agentFormFrom(atif);
    expect(editAgentBody(values, atif)).not.toHaveProperty("trajectory_source");
    const otel = { ...values, trajectory_source: "otel" as const };
    expect(editAgentBody(otel, atif)).toMatchObject({
      trajectory_source: "otel",
    });
    expect(editAgentBody(otel, atif)).not.toHaveProperty("mapping");
    expect(
      editAgentBody({ ...otel, mapping: '{"session_id": {}}' }, atif),
    ).toMatchObject({ trajectory_source: "otel", mapping: { session_id: {} } });
    expect(agentFormErrors({ ...otel, mapping: "[1]" }, "edit")).toContain(
      "The span mapping is not a JSON object.",
    );
  });
});
