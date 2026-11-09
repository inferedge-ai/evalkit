import { useState } from "react";
import type { FormEvent } from "react";
import { Waypoints } from "lucide-react";
import { apiJson } from "../lib/api";
import { ENDPOINT_NOTE } from "../lib/endpoint";
import { refreshQueries, useApi } from "../lib/query";
import type { TraceSourceInfo } from "../lib/types";
import { useMe } from "../shell/capabilities";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const ROW_COLUMNS = "grid-cols-[minmax(0,1fr)_auto_minmax(0,2fr)_auto]";

export function TraceSourcesPage() {
  const me = useMe();
  const listed = useApi<TraceSourceInfo[]>("/trace-sources");
  const rows = listed.data;
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const error = actionError ?? listed.error?.message ?? null;

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
      <PageHeader
        title="Trace sources"
        description="Third-party trace stores to import history from. Keys are stored encrypted and never shown again."
      />
      <div className="page">
        {me.is_admin && (
          <>
            <form
              className="mb-2 flex flex-wrap items-center gap-2"
              onSubmit={create}
            >
              <Input
                className="w-full sm:w-40"
                placeholder="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
              <Input
                className="w-full sm:w-auto sm:min-w-48 sm:flex-1"
                placeholder="Phoenix URL"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
              <Input
                className="w-full sm:w-40"
                type="password"
                placeholder="API key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <Button type="submit">Add trace source</Button>
            </form>
            <p className="mb-3 max-w-3xl text-xs text-muted-foreground">
              {ENDPOINT_NOTE}
            </p>
          </>
        )}
        {error && <div className="pane-status error">{error}</div>}
        {rows === undefined && !listed.error && (
          <Card className="rows gap-0 py-0">
            {Array.from({ length: 2 }, (_, i) => (
              <div key={i} className={`row ${ROW_COLUMNS}`}>
                <Skeleton
                  className="h-4"
                  style={{ width: `${40 + i * 15}%` }}
                />
                <Skeleton className="h-4 w-14" />
                <Skeleton className="h-3 w-48 max-w-full" />
              </div>
            ))}
          </Card>
        )}
        {rows?.length === 0 && (
          <Card className="py-8">
            <Empty
              icon={Waypoints}
              title="No trace sources"
              description={
                me.is_admin ? (
                  <>
                    Add a Phoenix server above to import an agent's past traces,
                    or run{" "}
                    <span className="font-mono">
                      evalkit trace-sources create
                    </span>
                    .
                  </>
                ) : (
                  <>
                    An admin adds them with{" "}
                    <span className="font-mono">
                      evalkit trace-sources create
                    </span>
                    .
                  </>
                )
              }
            />
          </Card>
        )}
        {!!rows?.length && (
          <Card className="rows gap-0 py-0">
            {rows.map((row) => (
              <div key={row.id} className={`row ${ROW_COLUMNS}`}>
                <span className="row-strong">{row.name}</span>
                <Badge variant="secondary">{row.provider}</Badge>
                <span className="row-meta truncate font-mono">{row.url}</span>
                {me.is_admin && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => archive(row.name)}
                  >
                    archive
                  </Button>
                )}
              </div>
            ))}
          </Card>
        )}
      </div>
    </>
  );
}
