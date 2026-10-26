import { useMemo } from "react";
import type { CSSProperties } from "react";
import { timingAnalysis } from "../lib/analysis";
import type { TimingRow } from "../lib/analysis";
import { toModel } from "../lib/atif";
import { formatMs } from "../lib/format";
import type { AtifTrajectory } from "../lib/types";

function StepCells({ row, max }: { row: TimingRow; max: number }) {
  return (
    <>
      <td className="num col-num">#{row.step}</td>
      <td>{row.agent}</td>
      <td>{row.source}</td>
      <td className="max-w-80 truncate font-mono" title={row.tools.join(", ")}>
        {row.tools.join(", ")}
      </td>
      <td className="num col-num">{formatMs(row.start_ms)}</td>
      {/* Where the wall-clock went, as a shape you can scan. */}
      <td
        className="num col-num bar"
        style={
          {
            "--bar":
              max > 0 && row.duration_ms !== null ? row.duration_ms / max : 0,
          } as CSSProperties
        }
      >
        {row.duration_ms !== null ? formatMs(row.duration_ms) : "—"}
      </td>
    </>
  );
}

function StepTable({ title, rows }: { title: string; rows: TimingRow[] }) {
  if (rows.length === 0) return null;
  const max = Math.max(...rows.map((r) => r.duration_ms ?? 0), 0);
  return (
    <section>
      <div className="eyebrow">{title}</div>
      <div className="card">
        <table className="data-table">
          <thead>
            <tr>
              <th className="col-num">step</th>
              <th>agent</th>
              <th>source</th>
              <th>tools</th>
              <th className="col-num">start</th>
              <th className="col-num">duration</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.agent}-${r.step}`}>
                <StepCells row={r} max={max} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function TimingView({ doc }: { doc: AtifTrajectory }) {
  const timing = useMemo(() => timingAnalysis(toModel(doc)), [doc]);

  if (timing.timed_steps === 0) {
    return (
      <div className="pane-status">
        No step timestamps in this trajectory — timing needs at least two
        timestamped steps.
      </div>
    );
  }

  return (
    <div className="max-w-[68rem]">
      <div className="eyebrow">Wall clock</div>
      <div className="tile-grid">
        <div className="tile tile-hero">
          <div className="tile-label">total</div>
          <div className="tile-value">
            {timing.wall_ms !== null ? formatMs(timing.wall_ms) : "—"}
          </div>
        </div>
        <div className="tile">
          <div className="tile-label">steps</div>
          <div className="tile-value">{timing.steps}</div>
        </div>
        <div
          className="tile"
          title="steps whose duration is measurable — the gap to the next timestamped step"
        >
          <div className="tile-label">timed steps</div>
          <div className="tile-value">{timing.timed_steps}</div>
        </div>
      </div>
      <StepTable title="Slowest steps" rows={timing.slowest} />
      <StepTable title="All steps" rows={timing.rows} />
    </div>
  );
}
