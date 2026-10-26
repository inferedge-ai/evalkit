import type { AgentInfo, AgentSettings } from "./types";

export interface AgentFormValues {
  workspace: string;
  name: string;
  harness_url: string;
  trajectory_source: "atif" | "otel";
  repo_url: string;
  mapping: string;
  settings: Record<keyof Omit<AgentSettings, "provides">, string>;
}

export const SETTING_FIELDS: {
  key: keyof AgentFormValues["settings"];
  label: string;
  min: number;
}[] = [
  { key: "max_concurrent_runs", label: "Max concurrent runs", min: 1 },
  { key: "run_timeout_seconds", label: "Run timeout (s)", min: 0 },
  { key: "poll_interval_seconds", label: "Poll interval (s)", min: 0 },
  { key: "stop_grace_seconds", label: "Stop grace (s)", min: 0 },
  { key: "trajectory_wait_seconds", label: "Trajectory wait (s)", min: 0 },
];

const DEFAULTS: AgentFormValues["settings"] = {
  max_concurrent_runs: "4",
  run_timeout_seconds: "840",
  poll_interval_seconds: "5",
  stop_grace_seconds: "120",
  trajectory_wait_seconds: "60",
};

export function emptyAgentForm(workspace: string): AgentFormValues {
  return {
    workspace,
    name: "",
    harness_url: "",
    trajectory_source: "atif",
    repo_url: "",
    mapping: "",
    settings: { ...DEFAULTS },
  };
}

export function agentFormFrom(agent: AgentInfo): AgentFormValues {
  const settings = { ...DEFAULTS };
  for (const { key } of SETTING_FIELDS)
    settings[key] = String(agent.settings[key]);
  return {
    workspace: agent.workspace,
    name: agent.name,
    harness_url: agent.harness_url ?? "",
    trajectory_source: agent.trajectory_source,
    repo_url: agent.repo_url ?? "",
    mapping: "",
    settings,
  };
}

function parseMapping(text: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function agentFormErrors(
  values: AgentFormValues,
  mode: "create" | "edit",
): string[] {
  const errors: string[] = [];
  if (mode === "create") {
    if (!values.name.trim()) errors.push("Name is required.");
    else if (values.name.includes("@")) errors.push("Name cannot contain @.");
    if (values.trajectory_source === "otel") {
      if (!values.mapping.trim())
        errors.push(
          "An agent whose trajectories come from traces needs a span mapping.",
        );
      else if (!parseMapping(values.mapping))
        errors.push("The span mapping is not a JSON object.");
    }
  } else if (
    values.trajectory_source === "otel" &&
    values.mapping.trim() &&
    !parseMapping(values.mapping)
  ) {
    errors.push("The span mapping is not a JSON object.");
  }
  for (const { key, label, min } of SETTING_FIELDS) {
    const raw = values.settings[key].trim();
    const n = Number(raw);
    if (!raw || !Number.isInteger(n) || n < min)
      errors.push(`${label} must be a whole number of at least ${min}.`);
  }
  return errors;
}

function settingsOf(values: AgentFormValues): Omit<AgentSettings, "provides"> {
  const out = {} as Omit<AgentSettings, "provides">;
  for (const { key } of SETTING_FIELDS) out[key] = Number(values.settings[key]);
  return out;
}

export function createAgentBody(
  values: AgentFormValues,
): Record<string, unknown> {
  return {
    workspace: values.workspace,
    name: values.name.trim(),
    harness_url: values.harness_url.trim() || null,
    trajectory_source: values.trajectory_source,
    repo_url: values.repo_url.trim() || null,
    settings: settingsOf(values),
    mapping:
      values.trajectory_source === "otel" ? parseMapping(values.mapping) : null,
  };
}

export function editAgentBody(
  values: AgentFormValues,
  current: AgentInfo,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    harness_url: values.harness_url.trim() || null,
    repo_url: values.repo_url.trim() || null,
    settings: { ...current.settings, ...settingsOf(values) },
  };
  if (values.trajectory_source !== current.trajectory_source)
    body.trajectory_source = values.trajectory_source;
  if (values.trajectory_source === "otel" && values.mapping.trim())
    body.mapping = parseMapping(values.mapping);
  return body;
}
