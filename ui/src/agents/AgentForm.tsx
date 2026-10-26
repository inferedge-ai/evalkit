import { useEffect, useState } from "react";
import { apiJson } from "../lib/api";
import {
  agentFormErrors,
  agentFormFrom,
  createAgentBody,
  editAgentBody,
  emptyAgentForm,
  SETTING_FIELDS,
} from "../lib/agentForm";
import type { AgentFormValues } from "../lib/agentForm";
import { Select } from "../lib/Select";
import type { AgentInfo } from "../lib/types";

export function AgentForm({
  workspace,
  agent,
  onSaved,
  onClose,
}: {
  workspace: string;
  agent?: AgentInfo;
  onSaved: (agent: AgentInfo) => void;
  onClose: () => void;
}) {
  const mode = agent ? "edit" : "create";
  const [values, setValues] = useState<AgentFormValues>(
    agent ? agentFormFrom(agent) : emptyAgentForm(workspace),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const errors = agentFormErrors(values, mode);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = <K extends keyof AgentFormValues>(
    key: K,
    value: AgentFormValues[K],
  ) => setValues((v) => ({ ...v, [key]: value }));

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = agent
        ? await apiJson<AgentInfo>(
            `/agents/${encodeURIComponent(agent.ref)}`,
            "PATCH",
            editAgentBody(values, agent),
          )
        : await apiJson<AgentInfo>("/agents", "POST", createAgentBody(values));
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <div
        className="dialog w-160"
        role="dialog"
        aria-label={agent ? "Edit agent" : "New agent"}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="dialog-head">
          <span className="font-semibold">
            {agent ? "Edit agent" : "New agent"}
          </span>
          <span className="row-meta font-mono">
            {agent ? agent.ref : `@${values.workspace}`}
          </span>
          <button
            className="btn btn-quiet btn-sm ml-auto"
            aria-label="close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <div className="dialog-body">
          {!agent && (
            <div className="form-row">
              <span className="form-label">Name</span>
              <input
                className="input w-72"
                value={values.name}
                onChange={(e) => set("name", e.target.value)}
              />
            </div>
          )}
          <div className="form-row">
            <span className="form-label">Harness URL</span>
            <input
              className="input min-w-0 flex-1"
              placeholder="empty for a code-driven agent (SDK runs only)"
              value={values.harness_url}
              onChange={(e) => set("harness_url", e.target.value)}
            />
          </div>
          <div className="form-row">
            <span className="form-label">Trajectories</span>
            <Select
              ariaLabel="trajectory source"
              value={values.trajectory_source}
              onChange={(source) => set("trajectory_source", source)}
              options={[
                { value: "atif", label: "ATIF from the agent" },
                { value: "otel", label: "From traces (OTel spans)" },
              ]}
            />
          </div>
          {agent && values.trajectory_source !== agent.trajectory_source && (
            <p className="row-meta">
              Experiments that already ran keep the source they ran with. The
              change is refused while one of this agent&apos;s experiments is
              running.
            </p>
          )}
          {values.trajectory_source === "otel" &&
            (!agent || agent.trajectory_source !== "otel") && (
              <div className="form-row items-start">
                <span className="form-label pt-1.5">Span mapping</span>
                <textarea
                  className="input min-w-0 flex-1 font-mono"
                  rows={6}
                  placeholder={
                    agent
                      ? "Paste the mapping JSON, or leave empty to reuse the agent's last active mapping"
                      : "Paste the mapping JSON (start from the connect-traces skill's gen_ai.json or openinference.json)"
                  }
                  value={values.mapping}
                  onChange={(e) => set("mapping", e.target.value)}
                />
              </div>
            )}
          <div className="form-row">
            <span className="form-label">Repository</span>
            <input
              className="input min-w-0 flex-1"
              placeholder="https://github.com/org/repo (optional)"
              value={values.repo_url}
              onChange={(e) => set("repo_url", e.target.value)}
            />
          </div>
          {SETTING_FIELDS.map(({ key, label }) => (
            <div key={key} className="form-row">
              <span className="form-label">{label}</span>
              <input
                className="input w-28"
                inputMode="numeric"
                value={values.settings[key]}
                onChange={(e) =>
                  set("settings", { ...values.settings, [key]: e.target.value })
                }
              />
            </div>
          ))}
          <p className="row-meta mt-2">
            The agent's API key is not stored here: add it to the{" "}
            <span className="font-mono">credentials.agents</span> map of the
            config that drives it.
          </p>
          {errors.map((message) => (
            <div key={message} className="text-meta text-err-text">
              {message}
            </div>
          ))}
          {error && <div className="text-meta text-err-text">{error}</div>}
        </div>
        <div className="dialog-foot">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || errors.length > 0}
            onClick={save}
          >
            {agent ? "Save" : "Create agent"}
          </button>
        </div>
      </div>
    </div>
  );
}
