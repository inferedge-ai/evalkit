import { useCallback, useEffect, useState } from "react";
import { api, apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { Select } from "../lib/Select";
import { messageCounts } from "../lib/esfSummary";
import { mappingRows, ruleNotes, sameRule } from "../lib/mappingRules";
import type { MappingRecord, RunList, SessionTrajectory } from "../lib/types";
import { TrajectoryViewer } from "../runs/TrajectoryViewer";

export function Summary({ session }: { session: SessionTrajectory }) {
  const esf = session.esf ?? [];
  const kinds = new Map<string, number>();
  for (const s of esf) kinds.set(s.kind, (kinds.get(s.kind) ?? 0) + 1);
  const llm = esf.filter((s) => s.kind === "llm");
  const messages = messageCounts(esf);
  const sum = (key: "prompt_tokens" | "completion_tokens" | "cached_tokens") =>
    llm.reduce((n, s) => n + (s[key] ?? 0), 0);
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-lede">
      <dt className="text-ink-3">Spans</dt>
      <dd>
        {[...kinds].map(([k, n]) => `${k} ${n}`).join(" · ") || "none"}
        {session.settled ? "" : " (not settled)"}
      </dd>
      <dt className="text-ink-3">Messages</dt>
      <dd>
        input {messages.input} (each agent's first LLM call) · output{" "}
        {messages.output} · {messages.toolCalls} tool calls
      </dd>
      <dt className="text-ink-3">Tokens</dt>
      <dd>
        in {sum("prompt_tokens")} · out {sum("completion_tokens")} · cached{" "}
        {sum("cached_tokens")}
      </dd>
      <dt className="text-ink-3">Mapping</dt>
      <dd>
        {session.mapping_version != null
          ? `v${session.mapping_version}`
          : "none"}
      </dd>
      <dt className="text-ink-3">Unmapped</dt>
      <dd className="flex flex-wrap gap-1">
        {session.unmapped_attributes.length
          ? session.unmapped_attributes.map((k) => (
              <span key={k} className="chip muted font-mono">
                {k}
              </span>
            ))
          : "none"}
      </dd>
    </dl>
  );
}

function MappingRules({
  doc,
  baseline,
}: {
  doc: Record<string, unknown>;
  baseline: Record<string, unknown> | null;
}) {
  const before = baseline ? mappingRows(baseline) : null;
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Field</th>
          <th>Span attribute</th>
          <th>How it is read</th>
        </tr>
      </thead>
      <tbody>
        {mappingRows(doc).map((row, i) => {
          const changed =
            before !== null && !sameRule(row.rule, before[i].rule);
          return (
            <tr key={row.field}>
              <td className="whitespace-nowrap">
                {row.label}
                {changed && (
                  <span className="chip chip-info ml-2">changed</span>
                )}
              </td>
              <td className="font-mono">
                {row.rule ? (
                  row.rule.attribute
                ) : (
                  <span className="text-ink-3">not read</span>
                )}
              </td>
              <td>
                <div className="flex flex-wrap gap-1">
                  {row.rule &&
                    ruleNotes(row.rule).map((note) => (
                      <span key={note} className="chip muted">
                        {note}
                      </span>
                    ))}
                  {row.rule?.values &&
                    Object.entries(row.rule.values).map(([from, to]) => (
                      <span key={from} className="chip muted font-mono">
                        {from} → {to}
                      </span>
                    ))}
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Preview({
  label,
  session,
}: {
  label: string;
  session: SessionTrajectory | null;
}) {
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h2 className="font-medium">{label}</h2>
      {!session && <div className="pane-status">Loading…</div>}
      {session && (
        <>
          <div className="card p-3">
            <Summary session={session} />
          </div>
          <div className="card flex min-h-96 flex-col p-3">
            {session.trajectory ? (
              <TrajectoryViewer doc={session.trajectory} />
            ) : (
              <div className="pane-status">No spans in this session.</div>
            )}
          </div>
        </>
      )}
    </section>
  );
}

export function MappingsPanel({ agent }: { agent: string }) {
  const [versions, setVersions] = useState<MappingRecord[] | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const [sessions, setSessions] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [active, setActive] = useState<SessionTrajectory | null>(null);
  const [draft, setDraft] = useState<SessionTrajectory | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = `/agents/${encodeURIComponent(agent)}`;
  const hasDraft = versions?.some((v) => v.status === "draft") ?? false;

  const loadVersions = useCallback(
    () =>
      api<MappingRecord[]>(`${base}/mappings`)
        .then(setVersions)
        .catch((e) => setError(String(e))),
    [base],
  );

  useEffect(() => {
    loadVersions();
    api<RunList>(`${base}/runs?limit=10`)
      .then((list) => {
        const ids = list.runs.map((r) => r.run_id);
        setSessions(ids);
        setSelected((current) => current || ids[0] || "");
      })
      .catch((e) => setError(String(e)));
  }, [base, loadVersions]);

  useEffect(() => {
    if (!selected || versions === null) return;
    setActive(null);
    setDraft(null);
    const path = `${base}/sessions/${encodeURIComponent(selected)}/trajectory`;
    api<SessionTrajectory>(`${path}?mapping=active&esf=true`)
      .then(setActive)
      .catch((e) => setError(String(e)));
    if (hasDraft)
      api<SessionTrajectory>(`${path}?mapping=draft&esf=true`)
        .then(setDraft)
        .catch((e) => setError(String(e)));
  }, [base, selected, versions, hasDraft]);

  const activeRecord = versions?.find((v) => v.status === "active") ?? null;
  const shown = viewing ?? (activeRecord ? `v${activeRecord.version}` : null);
  const shownRecord = versions?.find((v) => `v${v.version}` === shown) ?? null;
  const shownDoc = shownRecord?.document ?? null;
  const baseline =
    shownRecord && activeRecord && shownRecord.id !== activeRecord.id
      ? activeRecord.document
      : null;
  const viewButton = (key: string) => (
    <button
      className={`btn ${shown === key ? "btn-primary" : ""}`}
      onClick={() => setViewing(key)}
    >
      {shown === key ? "Showing" : "View"}
    </button>
  );

  const activate = async (version: number) => {
    setBusy(true);
    setError(null);
    try {
      await apiJson(`${base}/mappings/${version}/activate`, "POST");
      await loadVersions();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {error && <div className="pane-status error">{error}</div>}
      <section className="flex flex-col gap-2">
        <h2 className="font-medium">Versions</h2>
        <div className="card rows">
          {versions?.map((v) => (
            <div
              key={v.id}
              className="row grid-cols-[auto_auto_1fr_auto_auto_auto]"
            >
              <span className="row-strong font-mono">v{v.version}</span>
              <span
                className={`chip ${v.status === "active" ? "chip-pass" : "muted"}`}
              >
                {v.status}
              </span>
              <span className="row-meta">by {v.created_by ?? "-"}</span>
              <span className="row-meta">{fmtWhen(v.created_at) ?? ""}</span>
              {v.status === "draft" ? (
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() => activate(v.version)}
                >
                  Activate
                </button>
              ) : (
                <span />
              )}
              {viewButton(`v${v.version}`)}
            </div>
          ))}
        </div>
        {versions && versions.length === 0 && (
          <p className="text-meta text-ink-3">
            No mapping yet, so this agent's spans are stored without a session
            id. Push one with <code>evalkit traces mapping push</code>, starting
            from the connect-traces skill's <code>gen_ai.json</code> or{" "}
            <code>openinference.json</code>.
          </p>
        )}
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="font-medium">
          Rules:{" "}
          {shownRecord
            ? `v${shownRecord.version} (${shownRecord.status})`
            : "no mapping"}
        </h2>
        <p className="text-meta text-ink-3">
          Which span attribute fills each field of a converted span.
          {baseline &&
            " Rows marked changed differ from the active mapping."}{" "}
          The session id comes from the mapping's <code>session_id</code> rule.
        </p>
        <div className="card">
          {shownDoc ? (
            <MappingRules doc={shownDoc} baseline={baseline} />
          ) : (
            <div className="pane-status">
              {versions === null ? "Loading…" : "No mapping to show."}
            </div>
          )}
        </div>
      </section>
      <section className="flex flex-col gap-2">
        <div className="toolbar">
          <h2 className="font-medium">Preview over recent sessions</h2>
          <Select
            ariaLabel="session"
            value={selected}
            onChange={setSelected}
            placeholder="No sessions"
            options={sessions.map((id) => ({ value: id, label: id }))}
          />
        </div>
        {selected && (
          <div
            className={`grid gap-4 ${hasDraft ? "lg:grid-cols-2" : "grid-cols-1"}`}
          >
            <Preview label="Active mapping" session={active} />
            {hasDraft && <Preview label="Draft mapping" session={draft} />}
          </div>
        )}
      </section>
    </div>
  );
}
