import { afterEach, describe, expect, it } from "vitest";
import { agentCommitUrl, resetMetaForTests } from "./meta";

afterEach(() => resetMetaForTests(null));

describe("agentCommitUrl", () => {
  it("builds a commit url from the agent's repo and the sha", () => {
    resetMetaForTests({
      accent_color: null,
      agents: ["myagent@dev"],
      agent_repos: { "myagent@dev": "https://github.com/inferedge-ai/evalkit" },
    });
    expect(agentCommitUrl("myagent@dev", "aa2a0e10ffc6")).toBe(
      "https://github.com/inferedge-ai/evalkit/commit/aa2a0e10ffc6",
    );
  });

  it("returns null when the repo, agent, or sha is unknown", () => {
    resetMetaForTests({
      accent_color: null,
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
