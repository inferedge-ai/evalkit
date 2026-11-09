import { useState } from "react";
import type { FormEvent } from "react";
import { apiJson } from "../lib/api";
import { ENDPOINT_NOTE } from "../lib/endpoint";
import { refreshQueries, useApi } from "../lib/query";
import type { TraceSourceInfo } from "../lib/types";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

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
          <Input
            className="w-auto"
            placeholder="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <Input
            className="min-w-0 flex-1"
            placeholder="Phoenix URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            required
          />
          <Input
            className="w-auto"
            type="password"
            placeholder="API key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <Button type="submit">Add trace source</Button>
        </form>
        <p className="row-meta mb-3 whitespace-normal">{ENDPOINT_NOTE}</p>
        {error && <div className="pane-status error">{error}</div>}
        <Card className="rows gap-0 py-0">
          {rows?.map((row) => (
            <div
              key={row.id}
              className="row grid-cols-[minmax(0,1fr)_auto_minmax(0,2fr)_auto]"
            >
              <span className="row-strong">{row.name}</span>
              <Badge variant="secondary">{row.provider}</Badge>
              <span className="row-meta truncate font-mono">{row.url}</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => archive(row.name)}
              >
                archive
              </Button>
            </div>
          ))}
          {rows && !rows.length && (
            <div className="pane-status">No trace sources.</div>
          )}
        </Card>
      </div>
    </>
  );
}
