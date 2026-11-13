import { Badge } from "@/components/ui/badge";
import { useId, useState } from "react";
import { ArrowUpRight, Cable, History } from "lucide-react";
import { Link } from "react-router-dom";
import { apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { refreshQueries, useApi } from "../lib/query";
import { Select } from "../lib/Select";
import { ToArrow } from "../lib/ui";
import {
  importAction,
  importFraction,
  isActiveImport,
  localToIso,
} from "../lib/traceImports";
import type { TraceImport } from "../lib/traceImports";
import type {
  AgentSettings,
  SessionTrajectory,
  TraceSourceInfo,
} from "../lib/types";
import { Summary } from "./MappingsPanel";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Empty } from "@/components/widgets/Empty";
import {
  Field,
  FieldDescription,
  FieldLabel,
  FieldTitle,
} from "@/components/ui/field";

const POLL_MS = 2000;

type Mode = "session" | "range";

function SessionCheck({ agent, session }: { agent: string; session: string }) {
  const loaded = useApi<SessionTrajectory>(
    `/agents/${encodeURIComponent(agent)}/sessions/${encodeURIComponent(session)}/trajectory?esf=true`,
    { gcTime: 0 },
  );
  const trajectory = loaded.data;
  const error = loaded.error?.message;
  return (
    <div className="flex flex-col gap-2">
      <div className="toolbar">
        <span className="row-meta">
          Session <span className="font-mono">{session}</span>
        </span>
        <Link
          className={buttonVariants({ variant: "link" })}
          to={`/runs/${encodeURIComponent(agent)}/${encodeURIComponent(session)}`}
        >
          Trajectory <ArrowUpRight size={12} />
        </Link>
      </div>
      {error && <div className="pane-status error">{error}</div>}
      {trajectory && <Summary session={trajectory} />}
    </div>
  );
}

function ImportRow({
  item,
  agent,
  busy,
  act,
}: {
  item: TraceImport;
  agent: string;
  busy: boolean;
  act: (item: TraceImport, action: "stop" | "continue") => void;
}) {
  const [checking, setChecking] = useState(false);
  const action = importAction(item.status);
  const pct = Math.round(100 * importFraction(item));
  return (
    <div className="row gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 md:grid md:grid-cols-[auto_auto_1fr_auto_auto_auto]">
        <span className="row-strong font-mono">#{item.id}</span>
        <Badge
          variant={
            item.status === "done"
              ? "pass"
              : item.status === "failed"
                ? "fail"
                : "secondary"
          }
        >
          {item.status}
        </Badge>
        <span className="row-meta whitespace-normal">
          {item.source}/{item.project} · {fmtWhen(item.since)} <ToArrow />{" "}
          {fmtWhen(item.until)} · by {item.created_by}
        </span>
        <span className="flex items-center gap-2">
          <span
            className="relative h-2 w-40 overflow-hidden rounded bg-line-soft"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
          >
            <span
              className="absolute inset-y-0 left-0 bg-brand"
              style={{ width: `${pct}%` }}
            />
          </span>
          <span className="row-meta num">
            {pct}% · {item.traces} traces · {item.spans} spans
          </span>
        </span>
        {action ? (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => act(item, action)}
          >
            {action === "stop" ? "Stop" : "Continue"}
          </Button>
        ) : (
          <span />
        )}
        {item.status === "done" && item.sample_session ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setChecking((v) => !v)}
          >
            {checking ? "Hide check" : "Check a session"}
          </Button>
        ) : (
          <span />
        )}
      </div>
      {item.error && (
        <div className="pane-status error">
          {item.error}. Imported up to {fmtWhen(item.resume_from)}.
        </div>
      )}
      {checking && item.sample_session && (
        <SessionCheck agent={agent} session={item.sample_session} />
      )}
    </div>
  );
}

export function ImportPanel({
  agent,
  settings,
}: {
  agent: string;
  settings?: AgentSettings;
}) {
  const formId = useId();
  const base = `/agents/${encodeURIComponent(agent)}`;
  const [pickedSource, setPickedSource] = useState(
    settings?.trace_source ?? "",
  );
  const [project, setProject] = useState(settings?.project ?? "");
  const [mode, setMode] = useState<Mode>("range");
  const [sessionId, setSessionId] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [imported, setImported] = useState<{
    session: string;
    spans: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const listedSources = useApi<TraceSourceInfo[]>("/trace-sources");
  const sources = listedSources.data ?? [];
  const source = pickedSource || (sources[0]?.name ?? "");
  const listedImports = useApi<TraceImport[]>(`${base}/imports`, {
    refetchInterval: (q) =>
      q.state.data?.some((i) => isActiveImport(i.status)) ? POLL_MS : false,
  });
  const imports = listedImports.data ?? null;
  const importsFailed = imports === null && !!listedImports.error;

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setActionError(null);
    try {
      await work();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const submit = () =>
    run(async () => {
      if (mode === "session") {
        setImported(null);
        const body = await apiJson<{ imported: number }>(
          `${base}/sessions/import`,
          "POST",
          { source, project, session_id: sessionId },
        );
        setImported({ session: sessionId, spans: body.imported });
        return;
      }
      await apiJson<TraceImport>(`${base}/imports`, "POST", {
        source,
        project,
        since: localToIso(since),
        until: until ? localToIso(until) : new Date().toISOString(),
      });
      await refreshQueries();
    });

  const act = (item: TraceImport, action: "stop" | "continue") =>
    run(async () => {
      await apiJson<TraceImport>(
        `${base}/imports/${item.id}/${action}`,
        "POST",
      );
      await refreshQueries();
    });

  const ready =
    source &&
    project &&
    (mode === "session" ? sessionId.trim() : since) &&
    !busy;

  return (
    <div className="flex flex-col gap-4">
      {actionError && <div className="pane-status error">{actionError}</div>}
      <section className="flex max-w-3xl flex-col gap-2">
        <h2 className="font-medium">Import from a trace source</h2>
        {listedSources.error && (
          <div className="pane-status error">{listedSources.error.message}</div>
        )}
        {!listedSources.data ? (
          !listedSources.error && <Skeleton className="h-96 rounded-xl" />
        ) : listedSources.data.length === 0 ? (
          <Card className="py-8">
            <Empty
              icon={Cable}
              title="No trace sources yet"
              description={
                <>
                  An admin adds one on the Trace sources page or with{" "}
                  <span className="font-mono">
                    evalkit trace-sources create
                  </span>
                  .
                </>
              }
            />
          </Card>
        ) : (
          <Card size="sm">
            <CardContent className="flex flex-col gap-3">
              <Field>
                <FieldLabel htmlFor={`${formId}-source`}>Source</FieldLabel>
                <Select
                  id={`${formId}-source`}
                  ariaLabel="trace source"
                  value={source}
                  onChange={setPickedSource}
                  options={sources.map((s) => ({
                    value: s.name,
                    label: s.name,
                  }))}
                />
              </Field>
              <Field>
                <FieldLabel htmlFor={`${formId}-project`}>Project</FieldLabel>
                <Input
                  id={`${formId}-project`}
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                />
              </Field>
              <Field>
                <FieldTitle>What</FieldTitle>
                <div>
                  <div className="segment">
                    <button
                      aria-pressed={mode === "range"}
                      onClick={() => setMode("range")}
                    >
                      Time range
                    </button>
                    <button
                      aria-pressed={mode === "session"}
                      onClick={() => setMode("session")}
                    >
                      One session
                    </button>
                  </div>
                </div>
              </Field>
              {mode === "session" ? (
                <Field>
                  <FieldLabel htmlFor={`${formId}-session-id`}>
                    Session id
                  </FieldLabel>
                  <Input
                    id={`${formId}-session-id`}
                    className="font-mono"
                    value={sessionId}
                    onChange={(e) => setSessionId(e.target.value)}
                  />
                </Field>
              ) : (
                <>
                  <Field>
                    <FieldLabel htmlFor={`${formId}-since`}>Since</FieldLabel>
                    <Input
                      id={`${formId}-since`}
                      type="datetime-local"
                      value={since}
                      onChange={(e) => setSince(e.target.value)}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor={`${formId}-until`}>Until</FieldLabel>
                    <Input
                      id={`${formId}-until`}
                      type="datetime-local"
                      value={until}
                      onChange={(e) => setUntil(e.target.value)}
                    />
                    <FieldDescription>empty means now</FieldDescription>
                  </Field>
                  <p className="text-meta text-ink-3">
                    Imports every trace that started in this range, oldest
                    first, as a job on the server. Sessions come from the active
                    mapping's <code>session_id</code> rule. Leaving this page
                    does not stop it.
                  </p>
                </>
              )}
              <div>
                <Button disabled={!ready} onClick={submit}>
                  {mode === "session" ? "Import session" : "Start import"}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
        {imported && (
          <Card size="sm">
            <CardContent className="flex flex-col gap-2">
              <span>Imported {imported.spans} spans.</span>
              <SessionCheck agent={agent} session={imported.session} />
            </CardContent>
          </Card>
        )}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="font-medium">Range imports</h2>
        {listedImports.error && (
          <div className="pane-status error">{listedImports.error.message}</div>
        )}
        {!importsFailed && (
          <Card className="rows gap-0 py-0">
            {imports?.map((item) => (
              <ImportRow
                key={item.id}
                item={item}
                agent={agent}
                busy={busy}
                act={act}
              />
            ))}
            {imports && imports.length === 0 && (
              <Empty
                size="sm"
                icon={History}
                title="No range imports yet"
                className="py-6"
              />
            )}
            {imports === null &&
              Array.from({ length: 2 }, (_, i) => (
                <div key={i} className="row grid-cols-[auto_auto_1fr]">
                  <Skeleton className="h-4 w-10" />
                  <Skeleton className="h-4 w-14" />
                  <Skeleton className="h-3 w-2/3" />
                </div>
              ))}
          </Card>
        )}
      </section>
    </div>
  );
}
