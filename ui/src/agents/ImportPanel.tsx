import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { Select } from "../lib/Select";
import {
  importAction,
  importFraction,
  isActiveImport,
  localToIso,
} from "../lib/traceImports";
import type { TraceImport } from "../lib/traceImports";
import type { SessionTrajectory, TraceSourceInfo } from "../lib/types";
import { Summary } from "./MappingsPanel";

const POLL_MS = 2000;

type Mode = "session" | "range";

function SessionCheck({ agent, session }: { agent: string; session: string }) {
  const [trajectory, setTrajectory] = useState<SessionTrajectory | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setTrajectory(null);
    api<SessionTrajectory>(
      `/agents/${encodeURIComponent(agent)}/sessions/${encodeURIComponent(session)}/trajectory?esf=true`,
    )
      .then(setTrajectory)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [agent, session]);
  return (
    <div className="flex flex-col gap-2">
      <div className="toolbar">
        <span className="row-meta">
          Session <span className="font-mono">{session}</span>
        </span>
        <Link
          className="btn btn-quiet text-brand"
          to={`/runs/${encodeURIComponent(agent)}/${encodeURIComponent(session)}`}
        >
          Trajectory ↗
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
        <span
          className={`chip ${
            item.status === "done"
              ? "chip-pass"
              : item.status === "failed"
                ? "chip-fail"
                : "muted"
          }`}
        >
          {item.status}
        </span>
        <span className="row-meta">
          {item.source}/{item.project} · {fmtWhen(item.since)} →{" "}
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
          <button
            className="btn btn-sm"
            disabled={busy}
            onClick={() => act(item, action)}
          >
            {action === "stop" ? "Stop" : "Continue"}
          </button>
        ) : (
          <span />
        )}
        {item.status === "done" && item.sample_session ? (
          <button className="btn btn-sm" onClick={() => setChecking((v) => !v)}>
            {checking ? "Hide check" : "Check a session"}
          </button>
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
  const [sources, setSources] = useState<TraceSourceInfo[]>([]);
  const [source, setSource] = useState("");
  const [project, setProject] = useState("");
  const [mode, setMode] = useState<Mode>("range");
  const [sessionId, setSessionId] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [imports, setImports] = useState<TraceImport[] | null>(null);
  const [imported, setImported] = useState<{
    session: string;
    spans: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<TraceSourceInfo[]>("/trace-sources")
      .then((list) => {
        setSources(list);
        if (list.length) setSource((s) => s || list[0].name);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const load = useCallback(
    () =>
      api<TraceImport[]>(`${base}/imports`)
        .then(setImports)
        .catch((e) => setError(e instanceof Error ? e.message : String(e))),
    [base],
  );

  useEffect(() => {
    load();
  }, [load]);

  const active = imports?.some((i) => isActiveImport(i.status)) ?? false;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
      await load();
    });

  const act = (item: TraceImport, action: "stop" | "continue") =>
    run(async () => {
      await apiJson<TraceImport>(
        `${base}/imports/${item.id}/${action}`,
        "POST",
      );
      await load();
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
          <div className="card flex flex-col gap-2 p-3">
            <div className="form-row">
              <span className="form-label">Source</span>
              <Select
                ariaLabel="trace source"
                value={source}
                onChange={setSource}
                options={sources.map((s) => ({ value: s.name, label: s.name }))}
              />
            </div>
            <div className="form-row">
              <span className="form-label">Project</span>
              <input
                className="input w-72"
                value={project}
                onChange={(e) => setProject(e.target.value)}
              />
            </div>
            <div className="form-row">
              <span className="form-label">What</span>
              <div className="segment self-start">
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
            {mode === "session" ? (
              <div className="form-row">
                <span className="form-label">Session id</span>
                <input
                  className="input w-72 font-mono"
                  value={sessionId}
                  onChange={(e) => setSessionId(e.target.value)}
                />
              </div>
            ) : (
              <>
                <div className="form-row">
                  <span className="form-label">Since</span>
                  <input
                    type="datetime-local"
                    className="input"
                    value={since}
                    onChange={(e) => setSince(e.target.value)}
                  />
                </div>
                <div className="form-row">
                  <span className="form-label">Until</span>
                  <input
                    type="datetime-local"
                    className="input"
                    value={until}
                    onChange={(e) => setUntil(e.target.value)}
                  />
                  <span className="row-meta">empty means now</span>
                </div>
                <p className="text-meta text-ink-3">
                  Imports every trace that started in this range, oldest first,
                  as a job on the server. Sessions come from the active
                  mapping's <code>session_id</code> rule. Leaving this page does
                  not stop it.
                </p>
              </>
            )}
            <div>
              <button
                className="btn btn-primary"
                disabled={!ready}
                onClick={submit}
              >
                {mode === "session" ? "Import session" : "Start import"}
              </button>
            </div>
          </div>
        )}
        {imported && (
          <div className="card flex flex-col gap-2 p-3">
            <span>Imported {imported.spans} spans.</span>
            <SessionCheck agent={agent} session={imported.session} />
          </div>
        )}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="font-medium">Range imports</h2>
        <div className="card rows">
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
        </div>
      </section>
    </div>
  );
}
