import { describe, expect, it } from "vitest";
import { agentKind, agentTabs, isAgentTab } from "./agents";

describe("agentTabs", () => {
  it("adds mappings for otel agents and check for harness agents", () => {
    expect(
      agentTabs({ harness_url: null, trajectory_source: "atif" }).map(
        (t) => t.id,
      ),
    ).toEqual(["overview"]);
    expect(
      agentTabs({ harness_url: null, trajectory_source: "otel" }).map(
        (t) => t.id,
      ),
    ).toEqual(["overview", "mappings", "import"]);
    expect(
      agentTabs({ harness_url: "http://h", trajectory_source: "otel" }).map(
        (t) => t.id,
      ),
    ).toEqual(["overview", "mappings", "import", "check"]);
    expect(isAgentTab("check")).toBe(true);
    expect(isAgentTab("import")).toBe(true);
    expect(isAgentTab("runs")).toBe(false);
  });
});

describe("agentKind", () => {
  it("names the drive and the trajectory source", () => {
    expect(
      agentKind({ harness_url: "http://h", trajectory_source: "atif" }),
    ).toBe("Harness API · ATIF from the agent");
    expect(agentKind({ harness_url: null, trajectory_source: "otel" })).toBe(
      "SDK · traces",
    );
  });
});
