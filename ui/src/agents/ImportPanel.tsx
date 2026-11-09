import { Badge } from "@/components/ui/badge";
import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
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
import type { SessionTrajectory, TraceSourceInfo } from "../lib/types";
import { Summary } from "./MappingsPanel";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";

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
          className={cn(buttonVariants({ variant: "ghost" }), "text-brand")}
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
      <div className="grid grid-cols-[auto_auto_1fr_auto_auto_auto] items-center gap-3">
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
        <span className="row-meta">
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

export function ImportPanel({ agent }: { agent: string }) {
  const base = `/agents/${encodeURIComponent(agent)}`;
  const [pickedSource, setPickedSource] = useState("");
  const [project, setProject] = useState("");
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
  const error =
    actionError ??
    listedSources.error?.message ??
    listedImports.error?.message ??
    null;

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
      {error && <div className="pane-status error">{error}</div>}
      <section className="flex max-w-3xl flex-col gap-2">
        <h2 className="font-medium">Import from a trace source</h2>
        {sources.length === 0 ? (
          <p className="text-meta text-ink-3">
            No trace sources yet. An admin adds one on the Trace sources page or
            with <code>evalkit trace-sources create</code>.
          </p>
        ) : (
          <Card size="sm">
            <CardContent className="flex flex-col gap-3">
              <Field>
                <FieldLabel>Source</FieldLabel>
                <Select
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
                <FieldLabel>Project</FieldLabel>
                <Input
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                />
              </Field>
              <Field>
                <FieldLabel>What</FieldLabel>
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
                  <FieldLabel>Session id</FieldLabel>
                  <Input
                    className="font-mono"
                    value={sessionId}
                    onChange={(e) => setSessionId(e.target.value)}
                  />
                </Field>
              ) : (
                <>
                  <Field>
                    <FieldLabel>Since</FieldLabel>
                    <Input
                      type="datetime-local"
                      value={since}
                      onChange={(e) => setSince(e.target.value)}
                    />
                  </Field>
                  <Field>
                    <FieldLabel>Until</FieldLabel>
                    <Input
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
            <div className="pane-status">No range imports yet.</div>
          )}
          {imports === null && <div className="pane-status">Loading…</div>}
        </Card>
      </section>
    </div>
  );
}
