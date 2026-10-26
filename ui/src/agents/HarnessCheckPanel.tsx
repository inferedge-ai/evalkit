import { useState } from "react";
import { apiJson } from "../lib/api";
import type { HarnessCheckReport } from "../lib/types";

const GLYPH = { pass: "✓", warn: "!", fail: "✗", skipped: "—" } as const;
const CLS = {
  pass: "chip-pass",
  warn: "chip-partial",
  fail: "chip-fail",
  skipped: "chip-none",
} as const;

export function HarnessCheckPanel({ agent }: { agent: string }) {
  const [report, setReport] = useState<HarnessCheckReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setReport(null);
    setError(null);
    try {
      setReport(
        await apiJson<HarnessCheckReport>(
          `/agents/${encodeURIComponent(agent)}/check`,
          "POST",
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="toolbar">
        <button className="btn btn-primary" disabled={busy} onClick={run}>
          {busy ? "Checking… (runs a live agent call)" : "Run check"}
        </button>
        <p className="text-meta text-ink-3">
          Exercises the agent's Harness API: capabilities, the probe input
          against its input schema, a trivial run to a terminal status, the
          trajectory (from the harness or from traces), then files / run listing
          / stop where declared.
        </p>
      </div>
      {error && <div className="pane-status error">{error}</div>}
      {report && (
        <div className="card">
          <div className="rows">
            {Object.entries(report.checks).map(([capability, entry]) => (
              <div
                key={capability}
                className="row grid-cols-[8rem_auto_minmax(0,1fr)]"
              >
                <span className="font-mono">{capability}</span>
                <span className={`chip ${CLS[entry.status]}`}>
                  {GLYPH[entry.status]} {entry.status}
                </span>
                <span className="truncate text-ink-3" title={entry.detail}>
                  {entry.detail}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
