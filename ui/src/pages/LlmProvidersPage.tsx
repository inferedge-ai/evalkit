import { useState } from "react";
import type { FormEvent } from "react";
import { BrainCircuit } from "lucide-react";
import { apiJson, NotFoundError } from "../lib/api";
import { refreshQueries, setApiData, useApi } from "../lib/query";
import { useMe } from "../shell/capabilities";
import { LlmProviderForm } from "./LlmProviderForm";
import { Select } from "../lib/Select";
import type { JudgeSettings, LlmProviderInfo } from "../lib/types";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const ROW_COLUMNS = "grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto_auto_auto]";

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

  if (!loaded)
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-8 w-28" />
      </div>
    );
  if (!editable) {
    return (
      <p className="text-sm">
        {status ?? `${model} on ${provider} (effort ${effort})`}
      </p>
    );
  }
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={save}>
      <Select
        ariaLabel="default judge provider"
        value={provider}
        onChange={setProvider}
        placeholder="provider"
        options={providers.map((p) => ({ value: p.name, label: p.name }))}
      />
      <Input
        className="w-full sm:w-48"
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
  const rows = listed.data;
  const [actionError, setActionError] = useState<string | null>(null);
  const error = actionError ?? listed.error?.message ?? null;
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
        description="OpenAI-compatible endpoints the server judges with."
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
        {rows && (
          <section className="mb-4">
            <div className="eyebrow">Default judge</div>
            <p className="mb-2 max-w-3xl text-xs text-muted-foreground">
              Judge-using evaluators run on the server with a provider from this
              list, unless a run brings its own judge key.
            </p>
            <DefaultJudge providers={active} editable={me.is_admin} />
          </section>
        )}
        <div className="eyebrow">Providers</div>
        <p className="mb-2 text-xs text-muted-foreground">
          Keys are stored encrypted and never shown again.
        </p>
        {error && <div className="pane-status error">{error}</div>}
        {rows === undefined && !listed.error && (
          <Card className="rows gap-0 py-0">
            {Array.from({ length: 2 }, (_, i) => (
              <div key={i} className={`row ${ROW_COLUMNS}`}>
                <Skeleton
                  className="h-4"
                  style={{ width: `${40 + i * 15}%` }}
                />
                <Skeleton className="h-3 w-48 max-w-full" />
                <Skeleton className="h-4 w-14" />
              </div>
            ))}
          </Card>
        )}
        {rows?.length === 0 && (
          <Card className="py-8">
            <Empty
              icon={BrainCircuit}
              title="No LLM providers"
              description={
                me.is_admin ? (
                  <>
                    Add one with New provider, or run{" "}
                    <span className="font-mono">
                      evalkit llm-providers create
                    </span>
                    .
                  </>
                ) : (
                  <>
                    An admin adds them with{" "}
                    <span className="font-mono">
                      evalkit llm-providers create
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
              <div key={row.name} className={`row ${ROW_COLUMNS}`}>
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
          </Card>
        )}
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
