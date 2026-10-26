import { useMemo } from "react";
import { costAnalysis } from "../lib/analysis";
import type { CostRollupRow } from "../lib/analysis";
import { toModel } from "../lib/atif";
import { formatTokens, formatTokensFull, formatUsd } from "../lib/format";
import type { AtifTrajectory } from "../lib/types";

function Tile({
  value,
  label,
  sub,
  title,
  hero = false,
}: {
  value: string;
  label: string;
  sub?: string;
  title?: string;
  hero?: boolean;
}) {
  return (
    <div className={`tile ${hero ? "tile-hero" : ""}`} title={title}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  );
}

function CostTable({ title, rows }: { title: string; rows: CostRollupRow[] }) {
  if (rows.length === 0) return null;
  const max = Math.max(...rows.map((r) => r.cost), 0);
  return (
    <section>
      <div className="eyebrow">{title}</div>
      <div className="card">
        <table className="data-table">
          <thead>
            <tr>
              <th>key</th>
              <th className="col-num">calls</th>
              <th className="col-num">input</th>
              <th className="col-num">output</th>
              <th className="col-num">cache read</th>
              <th className="col-num">cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td className="font-mono">{r.key}</td>
                <td className="num col-num">{r.calls}</td>
                <td className="num col-num">{formatTokensFull(r.input)}</td>
                <td className="num col-num">{formatTokensFull(r.output)}</td>
                <td className="num col-num">
                  {formatTokensFull(r.cache_read)}
                </td>
                {/* Share of the biggest row, drawn behind the figure: which
                    line dominates the bill is the question this table answers. */}
                <td
                  className="num col-num bar"
                  style={
                    {
                      "--bar": max > 0 ? r.cost / max : 0,
                    } as React.CSSProperties
                  }
                >
                  {r.unpriced ? "—" : formatUsd(r.cost)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function CostView({ doc }: { doc: AtifTrajectory }) {
  const analysis = useMemo(() => costAnalysis(toModel(doc)), [doc]);
  const s = analysis.summary;

  return (
    <div className="max-w-[68rem]">
      <div className="eyebrow">Spend</div>
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
        <Tile
          hero
          value={s.cost !== null ? formatUsd(s.cost) : "—"}
          label="cost"
          sub={`${s.calls} llm calls · ${s.steps} steps`}
          title="Sum of the per-step cost_usd metrics the harness reported in the trajectory."
        />
      </div>

      <div className="eyebrow">Tokens</div>
      <div className="tile-grid">
        <Tile value={formatTokens(s.input)} label="input" />
        <Tile value={formatTokens(s.output)} label="output" />
        <Tile value={formatTokens(s.cache_read)} label="cache read" />
        <Tile
          value={String(s.models.length)}
          label="models"
          sub={s.models.join(", ")}
          title={s.models.join(", ")}
        />
      </div>

      {s.unpriced_models.length > 0 && (
        <div className="mt-2 text-meta text-warn">
          Unpriced models (token counts only): {s.unpriced_models.join(", ")}
        </div>
      )}

      <CostTable title="Per model" rows={analysis.by_model} />
      {analysis.by_agent.length > 1 && (
        <CostTable title="Per agent" rows={analysis.by_agent} />
      )}

      {analysis.tool_calls.length > 0 && (
        <section>
          <div className="eyebrow">Tool calls</div>
          <div className="card">
            <table className="data-table">
              <thead>
                <tr>
                  <th>tool</th>
                  <th className="col-num">calls</th>
                  <th className="col-num">errors</th>
                </tr>
              </thead>
              <tbody>
                {analysis.tool_calls.map((r) => (
                  <tr key={r.key}>
                    <td className="font-mono">{r.key}</td>
                    <td className="num col-num">{r.calls}</td>
                    <td
                      className={`num col-num ${r.errors ? "text-err-text" : ""}`}
                    >
                      {r.errors || ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
