import { fmtDelta, fmtGauge, fmtScore, severity } from "../lib/format";
import {
  bucketCards,
  compareCards,
  delta,
  hasScores,
} from "../lib/scoreSummary";
import type { Report } from "../lib/types";
import { InfoTip } from "../lib/ui";

export const HEADLINE_GAUGES = [
  "gauge_agent_cost_usd",
  "gauge_duration_seconds",
];

const gaugeLabel = (name: string) =>
  name.replace(/^gauge_/, "").replaceAll("_", " ");

function Delta({ value }: { value: number | null }) {
  if (value === null || Math.abs(value) < 0.005) return null;
  return (
    <span className={`num ${value > 0 ? "text-ok" : "text-err-text"}`}>
      {" "}
      {fmtDelta(value)}
    </span>
  );
}

export function BucketCards({
  report,
  onPick,
}: {
  report: Report;
  onPick?: (evaluator: string) => void;
}) {
  const total = report.cases.length;
  const cards = bucketCards(report);
  if (!cards.length) return null;
  return (
    <div className="bucket-cards">
      {cards.map((card) => (
        <div className="tile tile-hero" key={card.bucket}>
          <div className="flex items-baseline gap-1">
            <div className="tile-label">{card.bucket}</div>
            <InfoTip term={`bucket:${card.bucket}`} />
            <div
              className={`ml-auto tile-value sev-fg-${severity(card.score)}`}
            >
              {fmtScore(card.score)}
            </div>
          </div>
          <ul className="bucket-members">
            {card.members.map((m) => (
              <li key={m.name}>
                <button
                  type="button"
                  onClick={() => onPick?.(m.name)}
                  title={`focus ${m.name} in the case table`}
                >
                  <span className="member-name truncate font-mono">
                    {m.name}
                  </span>
                  {m.cases < total && (
                    <span className="text-ink-4">
                      n={m.cases}/{total}
                    </span>
                  )}
                  {m.errors > 0 && (
                    <span className="text-err-text">{m.errors} err</span>
                  )}
                  <span className={`num sev-fg-${severity(m.score)}`}>
                    {fmtScore(m.score)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function CompareBucketCards({
  base,
  compare,
}: {
  base: Report;
  compare: Report;
}) {
  const cards = compareCards(base, compare);
  const bothScored = hasScores(base) && hasScores(compare);
  if (!cards.length) return null;
  return (
    <div className="bucket-cards">
      {cards.map((card) => (
        <div className="tile tile-hero" key={card.bucket}>
          <div className="flex items-baseline gap-1">
            <div className="tile-label">{card.bucket}</div>
            <InfoTip term={`bucket:${card.bucket}`} />
            <div
              className={`ml-auto tile-value num sev-fg-${severity(card.compare)}`}
            >
              {fmtScore(card.compare)}
            </div>
          </div>
          {card.base !== null && card.compare !== null && (
            <div className="tile-sub num text-right">
              was {fmtScore(card.base)}
              <Delta value={delta(card.base, card.compare)} />
            </div>
          )}
          <ul className="bucket-members">
            {card.members.map((m) => (
              <li key={m.name}>
                <span className="member-name truncate font-mono">{m.name}</span>
                {m.side === "both" ? (
                  <span className="num">
                    <span className="text-ink-3">{fmtScore(m.base)} → </span>
                    <span className={`sev-fg-${severity(m.compare)}`}>
                      {fmtScore(m.compare)}
                    </span>
                    <Delta value={delta(m.base, m.compare)} />
                  </span>
                ) : (
                  <span className="num">
                    {bothScored && (
                      <span className="text-ink-4">only in {m.side} </span>
                    )}
                    {fmtScore(m.side === "base" ? m.base : m.compare)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function CostSpeedLine({
  report,
  base,
}: {
  report: Report;
  base?: Report;
}) {
  const shown = HEADLINE_GAUGES.map((name) => ({
    name,
    now: report.gauges.find((g) => g.name === name && g.count > 0),
    was: base?.gauges.find((g) => g.name === name && g.count > 0),
  })).filter((g) => g.now || g.was);
  if (!shown.length) return null;
  return (
    <div className="mt-2 flex flex-wrap items-baseline gap-x-6 gap-y-1 text-meta">
      {shown.map(({ name, now, was }) => (
        <span key={name} className="flex items-baseline gap-1.5">
          <span className="tile-label">{gaugeLabel(name)}</span>
          <InfoTip term={name} />
          {was && (
            <span className="num text-ink-3">
              {fmtGauge(name, was.median)} →
            </span>
          )}
          <span className="num font-semibold">
            {fmtGauge(name, now?.median ?? null)}
          </span>
          {was && now && (
            <span className="num text-ink-3">
              {fmtDelta(delta(was?.median, now?.median)) ?? ""}
            </span>
          )}
          {!base && now && (
            <span className="num text-micro text-ink-3">
              median · mean {fmtGauge(name, now.mean)} · n={now.count}
            </span>
          )}
        </span>
      ))}
    </div>
  );
}
