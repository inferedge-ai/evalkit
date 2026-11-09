import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiJson } from "../lib/api";
import {
  agentFormErrors,
  agentFormFrom,
  createAgentBody,
  editAgentBody,
  emptyAgentForm,
  SETTING_FIELDS,
  urlClearsKey,
} from "../lib/agentForm";
import { ApiKeyField } from "../lib/ApiKeyField";
import { ENDPOINT_NOTE, PERSONAL_KEY_NOTE } from "../lib/endpoint";
import { FORM_DIALOG, FORM_DIALOG_BODY } from "../lib/formDialog";
import type { AgentFormValues } from "../lib/agentForm";
import { Select } from "../lib/Select";
import type { AgentInfo } from "../lib/types";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";

export function AgentForm({
  workspace,
  shared,
  agent,
  onSaved,
  onClose,
}: {
  workspace: string;
  shared: boolean;
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
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={FORM_DIALOG}>
        <DialogHeader>
          <DialogTitle>{agent ? "Edit agent" : "New agent"}</DialogTitle>
          <DialogDescription className="font-mono">
            {agent ? agent.ref : `@${values.workspace}`}
          </DialogDescription>
        </DialogHeader>
        <div className={FORM_DIALOG_BODY}>
          {!agent && (
            <Field>
              <FieldLabel>Name</FieldLabel>
              <Input
                value={values.name}
                onChange={(e) => set("name", e.target.value)}
              />
            </Field>
          )}
          <Field>
            <FieldLabel>Harness URL</FieldLabel>
            <Input
              placeholder="empty for a code-driven agent (SDK runs only)"
              value={values.harness_url}
              onChange={(e) => set("harness_url", e.target.value)}
            />
            <FieldDescription>{ENDPOINT_NOTE}</FieldDescription>
          </Field>
          {shared ? (
            <Field>
              <FieldLabel>API key</FieldLabel>
              <ApiKeyField
                hasKey={agent?.has_api_key ?? false}
                value={values.api_key}
                onChange={(apiKey) => set("api_key", apiKey)}
              />
            </Field>
          ) : (
            <p className="row-meta whitespace-normal">{PERSONAL_KEY_NOTE}</p>
          )}
          {agent && urlClearsKey(values, agent) && (
            <p className="row-meta whitespace-normal text-err-text">
              Saving a new harness URL removes the stored API key. Enter the key
              for the new URL above.
            </p>
          )}
          <Field>
            <FieldLabel>Trajectories</FieldLabel>
            <Select
              ariaLabel="trajectory source"
              value={values.trajectory_source}
              onChange={(source) => set("trajectory_source", source)}
              options={[
                { value: "atif", label: "ATIF from the agent" },
                { value: "otel", label: "From traces (OTel spans)" },
              ]}
            />
          </Field>
          {agent && values.trajectory_source !== agent.trajectory_source && (
            <p className="row-meta">
              Experiments that already ran keep the source they ran with. The
              change is refused while one of this agent&apos;s experiments is
              running.
            </p>
          )}
          {values.trajectory_source === "otel" &&
            (!agent || agent.trajectory_source !== "otel") && (
              <Field>
                <FieldLabel>Span mapping</FieldLabel>
                <Textarea
                  className="font-mono"
                  rows={6}
                  placeholder={
                    agent
                      ? "Paste the mapping JSON, or leave empty to reuse the agent's last active mapping"
                      : "Paste the mapping JSON (start from the connect-traces skill's gen_ai.json or openinference.json)"
                  }
                  value={values.mapping}
                  onChange={(e) => set("mapping", e.target.value)}
                />
              </Field>
            )}
          <Field>
            <FieldLabel>Repository</FieldLabel>
            <Input
              placeholder="https://github.com/org/repo (optional)"
              value={values.repo_url}
              onChange={(e) => set("repo_url", e.target.value)}
            />
          </Field>
          {SETTING_FIELDS.map(({ key, label }) => (
            <Field key={key}>
              <FieldLabel>{label}</FieldLabel>
              <Input
                inputMode="numeric"
                value={values.settings[key]}
                onChange={(e) =>
                  set("settings", { ...values.settings, [key]: e.target.value })
                }
              />
            </Field>
          ))}
          {errors.map((message) => (
            <div key={message} className="text-meta text-err-text">
              {message}
            </div>
          ))}
          {error && <div className="text-meta text-err-text">{error}</div>}
        </div>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          <Button disabled={busy || errors.length > 0} onClick={save}>
            {agent ? "Save" : "Create agent"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
