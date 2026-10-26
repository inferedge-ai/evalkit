import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, apiJson } from "../lib/api";
import type { TraceSourceInfo } from "../lib/types";

export function TraceSourcesPage() {
  const [rows, setRows] = useState<TraceSourceInfo[] | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api<TraceSourceInfo[]>("/trace-sources")
      .then(setRows)
      .catch((e) => setError(String(e)));
  };
  useEffect(load, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await apiJson("/trace-sources", "POST", {
        name: name.trim(),
        provider: "phoenix",
        url: url.trim(),
        api_key: apiKey || null,
      });
      setName("");
      setUrl("");
      setApiKey("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  const archive = async (source: string) => {
    setError(null);
    try {
      await apiJson(
        `/trace-sources/${encodeURIComponent(source)}/archive`,
        "POST",
      );
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="page">
      <h1 className="page-title">Trace sources</h1>
      <p className="row-meta mt-1">
        Third-party trace stores to import history from. Keys are stored
        encrypted and never shown again.
      </p>
      <form className="toolbar mt-3 mb-3" onSubmit={create}>
        <input
          className="input"
          placeholder="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <input
          className="input min-w-0 flex-1"
          placeholder="Phoenix URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          required
        />
        <input
          className="input"
          type="password"
          placeholder="API key"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <button className="btn btn-primary">Add trace source</button>
      </form>
      {error && <div className="pane-status error">{error}</div>}
      <div className="card rows">
        {rows?.map((row) => (
          <div
            key={row.id}
            className="row grid-cols-[minmax(0,1fr)_auto_minmax(0,2fr)_auto]"
          >
            <span className="row-strong">{row.name}</span>
            <span className="chip muted">{row.provider}</span>
            <span className="row-meta truncate font-mono">{row.url}</span>
            <button className="btn btn-sm" onClick={() => archive(row.name)}>
              archive
            </button>
          </div>
        ))}
        {rows && !rows.length && (
          <div className="pane-status">No trace sources.</div>
        )}
      </div>
    </div>
  );
}
