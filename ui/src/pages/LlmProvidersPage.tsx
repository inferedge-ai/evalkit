import { useState } from "react";
import type { FormEvent } from "react";
import { apiJson, NotFoundError } from "../lib/api";
import { refreshQueries, setApiData, useApi } from "../lib/query";
import { useMe } from "../shell/capabilities";
import { LlmProviderForm } from "./LlmProviderForm";
import { Select } from "../lib/Select";
import type { JudgeSettings, LlmProviderInfo } from "../lib/types";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

function DefaultJudge({
  providers,
  editable,
}: {
  providers: LlmProviderInfo[];
  editable: boolean;
}) {
  const current = useApi<JudgeSettings>("/judge-default");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("high");
  const [status, setStatus] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  // Fill the form once, from this visit's answer rather than a cached one; a
  // later refetch must not overwrite what the admin types.
  if (!loaded && current.isFetchedAfterMount) {
    setLoaded(true);
    if (current.data) {
      setProvider(current.data.provider ?? "");
      setModel(current.data.model);
      setEffort(current.data.reasoning_effort);
    } else
      setStatus(
        current.error instanceof NotFoundError
          ? "No default judge yet."
          : String(current.error),
      );
  }

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setStatus(null);
    try {
      const saved = await apiJson<JudgeSettings>("/judge-default", "PATCH", {
        provider,
        model: model.trim(),
        reasoning_effort: effort.trim(),
      });
      setModel(saved.model);
      await setApiData("/judge-default", saved);
      setStatus("Saved.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  if (!loaded) return null;
  if (!editable) {
    return (
      <p className="row-meta mb-3 whitespace-normal">
        Default judge: {status ?? `${model} on ${provider} (effort ${effort})`}
      </p>
    );
  }
  return (
    <form className="toolbar mb-3" onSubmit={save}>
      <span className="form-label">Default judge</span>
      <Select
        ariaLabel="default judge provider"
        value={provider}
        onChange={setProvider}
        placeholder="provider"
        options={providers.map((p) => ({ value: p.name, label: p.name }))}
      />
      <Input
        className="w-auto"
        placeholder="model"
        value={model}
        onChange={(e) => setModel(e.target.value)}
        required
      />
      <Input
        className="w-28"
        placeholder="effort"
        value={effort}
        onChange={(e) => setEffort(e.target.value)}
      />
      <Button type="submit" disabled={!provider}>
        Save
      </Button>
      {status && <span className="row-meta">{status}</span>}
    </form>
  );
}

export function LlmProvidersPage() {
  const me = useMe();
  const listed = useApi<LlmProviderInfo[]>(
    "/llm-providers?include_archived=true",
  );
  const rows = listed.data ?? null;
  const [actionError, setActionError] = useState<string | null>(null);
  const error = actionError ?? (listed.error ? String(listed.error) : null);
  const [editing, setEditing] = useState<LlmProviderInfo | "new" | null>(null);

  const toggleArchive = async (row: LlmProviderInfo) => {
    setActionError(null);
    try {
      await apiJson(
        `/llm-providers/${encodeURIComponent(row.name)}/${row.archived ? "restore" : "archive"}`,
        "POST",
      );
      void refreshQueries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const active = rows?.filter((r) => !r.archived) ?? [];
  return (
    <>
      <PageHeader
        title="LLM providers"
        actions={
          me.is_admin && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditing("new")}
            >
              New provider
            </Button>
          )
        }
      />
      <div className="page">
        <p className="row-meta mb-3 whitespace-normal">
          OpenAI-compatible endpoints the server judges with. Judge-using
          evaluators run on the server with a provider from this list, unless a
          run brings its own judge key. Keys are stored encrypted and never
          shown again.
        </p>
        {rows && <DefaultJudge providers={active} editable={me.is_admin} />}
        {error && <div className="pane-status error">{error}</div>}
        <div className="card rows">
          {rows?.map((row) => (
            <div
              key={row.name}
              className="row grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto_auto_auto]"
            >
              <span className="row-strong">{row.name}</span>
              <span className="row-meta truncate font-mono">
                {row.base_url}
              </span>
              <Badge variant="secondary">
                {row.archived
                  ? "archived"
                  : row.has_api_key
                    ? "key set"
                    : "no key"}
              </Badge>
              {me.is_admin ? (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={row.archived}
                    onClick={() => setEditing(row)}
                  >
                    edit
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => toggleArchive(row)}
                  >
                    {row.archived ? "restore" : "archive"}
                  </Button>
                </>
              ) : (
                <>
                  <span />
                  <span />
                </>
              )}
            </div>
          ))}
          {rows && !rows.length && (
            <div className="pane-status">No LLM providers.</div>
          )}
        </div>
        {editing && (
          <LlmProviderForm
            provider={editing === "new" ? undefined : editing}
            onSaved={() => {
              setEditing(null);
              void refreshQueries();
            }}
            onClose={() => setEditing(null)}
          />
        )}
      </div>
    </>
  );
}
