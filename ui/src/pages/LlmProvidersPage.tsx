import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, apiJson } from "../lib/api";
import { ENDPOINT_NOTE } from "../lib/endpoint";
import { Select } from "../lib/Select";
import type { JudgeSettings, LlmProviderInfo } from "../lib/types";

function DefaultJudge({ providers }: { providers: LlmProviderInfo[] }) {
  const [judge, setJudge] = useState<JudgeSettings | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("high");
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    api<JudgeSettings>("/judge-default")
      .then((current) => {
        setJudge(current);
        setBaseUrl(current.base_url);
        setModel(current.model);
        setEffort(current.reasoning_effort);
      })
      .catch(() => setStatus("No default judge yet."));
  }, []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setStatus(null);
    try {
      const saved = await apiJson<JudgeSettings>("/judge-default", "PUT", {
        ...(judge ?? { max_tokens: 32768, concurrency: 16 }),
        base_url: baseUrl,
        model: model.trim(),
        reasoning_effort: effort.trim(),
      });
      setJudge(saved);
      setStatus("Saved.");
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <form className="toolbar mb-3" onSubmit={save}>
      <span className="form-label">Default judge</span>
      <Select
        ariaLabel="default judge provider"
        value={baseUrl}
        onChange={setBaseUrl}
        placeholder="provider"
        options={providers.map((p) => ({ value: p.base_url, label: p.name }))}
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
      <button className="btn btn-primary" disabled={!baseUrl}>
        Save
      </button>
      {status && <span className="row-meta">{status}</span>}
    </form>
  );
}

export function LlmProvidersPage() {
  const [rows, setRows] = useState<LlmProviderInfo[] | null>(null);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api<LlmProviderInfo[]>("/llm-providers?include_archived=true")
      .then(setRows)
      .catch((e) => setError(String(e)));
  };
  useEffect(load, []);

  const run = async (call: () => Promise<unknown>) => {
    setError(null);
    try {
      await call();
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const create = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await apiJson("/llm-providers", "POST", {
        name: name.trim(),
        base_url: baseUrl.trim(),
        ...(apiKey ? { api_key: apiKey } : {}),
      });
      setName("");
      setBaseUrl("");
      setApiKey("");
    });
  };
  const path = (provider: string, action = "") =>
    `/llm-providers/${encodeURIComponent(provider)}${action}`;
  const replaceKey = (provider: string) => {
    const key = window.prompt(
      `New API key for ${provider} (leave empty to remove the stored key)`,
    );
    if (key === null) return;
    void run(() => apiJson(path(provider), "PATCH", { api_key: key || null }));
  };

  const active = rows?.filter((r) => !r.archived) ?? [];
  return (
    <div className="page">
      <h1 className="page-title">LLM providers</h1>
      <p className="row-meta mt-1">
        OpenAI-compatible endpoints the server judges with. Server runs only use
        a judge from this list. Keys are stored encrypted and never shown again.
      </p>
      <form className="toolbar mt-3 mb-1" onSubmit={create}>
        <input
          className="input"
          placeholder="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <input
          className="input min-w-0 flex-1"
          placeholder="base URL, e.g. https://api.openai.com/v1"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          required
        />
        <input
          className="input"
          type="password"
          aria-label="API key"
          placeholder="API key"
          autoComplete="off"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <button className="btn btn-primary">Add provider</button>
      </form>
      <p className="row-meta mb-3">{ENDPOINT_NOTE}</p>
      {rows && <DefaultJudge providers={active} />}
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
              {row.has_api_key ? "key set" : "no key"}
            </span>
            <button
              className="btn btn-sm"
              disabled={row.archived}
              onClick={() => replaceKey(row.name)}
            >
              replace key
            </button>
            <button
              className="btn btn-sm"
              onClick={() =>
                void run(() =>
                  apiJson(
                    path(row.name, row.archived ? "/restore" : "/archive"),
                    "POST",
                  ),
                )
              }
            >
              {row.archived ? "restore" : "archive"}
            </button>
          </div>
        ))}
        {rows && !rows.length && (
          <div className="pane-status">No LLM providers.</div>
        )}
      </div>
    </div>
  );
}
