import { useState } from "react";
import type { FormEvent } from "react";
import { apiJson } from "../lib/api";
import { ENDPOINT_NOTE } from "../lib/endpoint";
import { refreshQueries, useApi } from "../lib/query";
import type { TraceSourceInfo } from "../lib/types";
import { PageHeader } from "@/components/widgets/PageHeader";

export function TraceSourcesPage() {
  const listed = useApi<TraceSourceInfo[]>("/trace-sources");
  const rows = listed.data ?? null;
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const error = actionError ?? (listed.error ? String(listed.error) : null);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setActionError(null);
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
      void refreshQueries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };
  const archive = async (source: string) => {
    setActionError(null);
    try {
      await apiJson(
        `/trace-sources/${encodeURIComponent(source)}/archive`,
        "POST",
      );
      void refreshQueries();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <PageHeader title="Trace sources" />
      <div className="page">
        <p className="row-meta">
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
        <p className="row-meta mb-3 whitespace-normal">{ENDPOINT_NOTE}</p>
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
    </>
  );
}
