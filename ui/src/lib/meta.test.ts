// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { resetProvidersForTest, setSessionForTest } from "../auth/oidc";
import { agentCommitUrl, loadAccent, resetMetaForTests } from "./meta";

afterEach(() => {
  resetMetaForTests(null);
  vi.unstubAllGlobals();
  resetProvidersForTest();
  setSessionForTest(undefined);
});

describe("loadAccent", () => {
  it("draws the deploy's accent from /api/appearance alone", async () => {
    const requested: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        requested.push(url);
        if (url.endsWith("/api/auth/providers"))
          return Response.json({ password: "all", oidc: null });
        return Response.json({ accent_color: "#7c3aed" });
      }),
    );

    await loadAccent();

    expect(requested).toEqual(["/api/appearance"]);
    expect(
      document.documentElement.style.getPropertyValue("--deploy-accent"),
    ).toBe("#7c3aed");
  });
});

describe("agentCommitUrl", () => {
  it("builds a commit url from the agent's repo and the sha", () => {
    resetMetaForTests({
      agents: ["myagent@dev"],
      agent_repos: { "myagent@dev": "https://github.com/inferedge-ai/evalkit" },
    });
    expect(agentCommitUrl("myagent@dev", "aa2a0e10ffc6")).toBe(
      "https://github.com/inferedge-ai/evalkit/commit/aa2a0e10ffc6",
    );
  });

  it("returns null when the repo, agent, or sha is unknown", () => {
    resetMetaForTests({
      agents: ["myagent@dev"],
      agent_repos: { "myagent@dev": "https://github.com/inferedge-ai/evalkit" },
    });
    expect(agentCommitUrl("mystery", "aa2a0e10")).toBe(null);
    expect(agentCommitUrl("myagent@alice", "aa2a0e10")).toBe(null);
    expect(agentCommitUrl("myagent@dev", null)).toBe(null);
    expect(agentCommitUrl(null, "aa2a0e10")).toBe(null);
    resetMetaForTests(null);
    expect(agentCommitUrl("myagent@dev", "aa2a0e10")).toBe(null);
  });
});
