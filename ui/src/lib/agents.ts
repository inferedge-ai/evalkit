import type { AgentInfo } from "./types";

export type AgentTab = "overview" | "mappings" | "import" | "check";

// Which tabs an agent page shows: Mappings and Import only when its
// trajectories come from collected spans, Check only when evalkit can drive
// it through the Harness API.
export function agentTabs(
  agent: Pick<AgentInfo, "harness_url" | "trajectory_source">,
): { id: AgentTab; label: string }[] {
  const tabs: { id: AgentTab; label: string }[] = [
    { id: "overview", label: "Overview" },
  ];
  if (agent.trajectory_source === "otel")
    tabs.push(
      { id: "mappings", label: "Mappings" },
      { id: "import", label: "Import" },
    );
  if (agent.harness_url) tabs.push({ id: "check", label: "Harness check" });
  return tabs;
}

export function isAgentTab(value: string | undefined): value is AgentTab {
  return (
    value === "overview" ||
    value === "mappings" ||
    value === "import" ||
    value === "check"
  );
}

// One line that says how evalkit reaches the agent and where trajectories
// come from, for list rows.
export function agentKind(
  agent: Pick<AgentInfo, "harness_url" | "trajectory_source">,
): string {
  const drive = agent.harness_url ? "Harness API" : "SDK";
  const source =
    agent.trajectory_source === "otel" ? "traces" : "ATIF from the agent";
  return `${drive} · ${source}`;
}
