import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, apiJson, NotFoundError } from "../lib/api";
import { useMe } from "../shell/capabilities";
import { LlmProviderForm } from "./LlmProviderForm";
import { Select } from "../lib/Select";
import type { JudgeSettings, LlmProviderInfo } from "../lib/types";

function DefaultJudge({
  providers,
  editable,
}: {
  providers: LlmProviderInfo[];
  editable: boolean;
}) {
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("high");
  const [status, setStatus] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api<JudgeSettings>("/judge-default")
      .then((current) => {
        setProvider(current.provider ?? "");
        setModel(current.model);
        setEffort(current.reasoning_effort);
      })
      .catch((e) =>
        setStatus(
          e instanceof NotFoundError ? "No default judge yet." : String(e),
        ),
      )
      .finally(() => setLoaded(true));
  }, []);

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
      <input
        className="input"
        placeholder="model"
        value={model}
        onChange={(e) => setModel(e.target.value)}
        required
      />
      <input
        className="input w-28"
        placeholder="effort"
        value={effort}
        onChange={(e) => setEffort(e.target.value)}
      />
      <button className="btn btn-primary" disabled={!provider}>
        Save
      </button>
      {status && <span className="row-meta">{status}</span>}
    </form>
  );
}

export function LlmProvidersPage() {
  const me = useMe();
  const [rows, setRows] = useState<LlmProviderInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<LlmProviderInfo | "new" | null>(null);

  const load = () => {
    api<LlmProviderInfo[]>("/llm-providers?include_archived=true")
      .then(setRows)
      .catch((e) => setError(String(e)));
  };
  useEffect(load, []);

  const toggleArchive = async (row: LlmProviderInfo) => {
    setError(null);
    try {
      await apiJson(
        `/llm-providers/${encodeURIComponent(row.name)}/${row.archived ? "restore" : "archive"}`,
        "POST",
      );
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const active = rows?.filter((r) => !r.archived) ?? [];
  return (
    <div className="page">
      <div className="toolbar mb-1">
        <h1 className="page-title">LLM providers</h1>
        {me.is_admin && (
          <button
            className="btn btn-sm ml-auto"
            onClick={() => setEditing("new")}
          >
            New provider
          </button>
        )}
      </div>
      <p className="row-meta mb-3 whitespace-normal">
        OpenAI-compatible endpoints the server judges with. Judge-using
        evaluators run on the server with a provider from this list, unless a
        run brings its own judge key. Keys are stored encrypted and never shown
        again.
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
            <span className="row-meta truncate font-mono">{row.base_url}</span>
            <span className="chip muted">
              {row.archived
                ? "archived"
                : row.has_api_key
                  ? "key set"
                  : "no key"}
            </span>
            {me.is_admin ? (
              <>
                <button
                  className="btn btn-sm"
                  disabled={row.archived}
                  onClick={() => setEditing(row)}
                >
                  edit
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => toggleArchive(row)}
                >
                  {row.archived ? "restore" : "archive"}
                </button>
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
            load();
          }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
