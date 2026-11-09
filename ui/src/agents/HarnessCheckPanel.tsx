import { Badge } from "@/components/ui/badge";
import { useState } from "react";
import { Check, Minus, TriangleAlert, X } from "lucide-react";
import { apiJson } from "../lib/api";
import type { HarnessCheckReport } from "../lib/types";
import { Button } from "@/components/ui/button";

const STATUS_ICON = {
  pass: Check,
  warn: TriangleAlert,
  fail: X,
  skipped: Minus,
} as const;
const VARIANT = {
  pass: "pass",
  warn: "partial",
  fail: "fail",
  skipped: "secondary",
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
        <Button disabled={busy} onClick={run}>
          {busy ? "Checking… (runs a live agent call)" : "Run check"}
        </Button>
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
            {Object.entries(report.checks).map(([capability, entry]) => {
              const Icon = STATUS_ICON[entry.status];
              return (
                <div
                  key={capability}
                  className="row grid-cols-[8rem_auto_minmax(0,1fr)]"
                >
                  <span className="font-mono">{capability}</span>
                  <Badge variant={VARIANT[entry.status]}>
                    <Icon size={11} /> {entry.status}
                  </Badge>
                  <span className="truncate text-ink-3" title={entry.detail}>
                    {entry.detail}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
