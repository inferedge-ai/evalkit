import { useMemo, useRef, useState } from "react";
import { timingAnalysis } from "../lib/analysis";
import { errorEntries, toModel } from "../lib/atif";
import type {
  ErrorEntry,
  StepRow,
  ToolCallRow,
  TrajectoryModel,
} from "../lib/atif";
import { formatElapsed, formatTokens, formatUsd, toYaml } from "../lib/format";
import type { AtifTrajectory } from "../lib/types";
import { Collapsible, Tabs } from "../lib/ui";
import { Markdown, PreBlock } from "../evals/bits";

function clock(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

function formatTokensShort(n: number): string {
  return n < 1000 ? String(n) : `${(n / 1000).toFixed(1)}k`;
}

function StepGutter({
  step,
  durationMs,
}: {
  step: StepRow;
  durationMs: number | null;
}) {
  const time = clock(step.timestamp);
  const { prompt, completion, cached } = step.tokens ?? {
    prompt: null,
    completion: null,
    cached: null,
  };
  return (
    <div className="num w-14 shrink-0 pt-1 text-right text-micro text-ink-4">
      {time && <div>{time}</div>}
      {durationMs !== null && durationMs > 0 && (
        <div
          className="text-ink-3"
          title="step duration (gap to the next step)"
        >
          {formatElapsed(durationMs)}
        </div>
      )}
      {completion !== null && (
        <div
          title={`input ${prompt ?? 0} · cache read ${cached ?? 0} · output ${completion}`}
        >
          ↓{formatTokensShort(completion)}
        </div>
      )}
      {step.costUsd !== null && (
        <div title="step cost">{formatUsd(step.costUsd)}</div>
      )}
    </div>
  );
}

function callKey(stepId: number, callId: string): string {
  return `${stepId}:${callId}`;
}

type StepKind = "system" | "user" | "agent" | "subagent";

function stepKind(step: StepRow, inSubagent: boolean): StepKind {
  return inSubagent && step.source === "agent" ? "subagent" : step.source;
}

function callKind(call: ToolCallRow): "error" | "subagent" | "tool" {
  if (call.isError) return "error";
  return call.subagents.length ? "subagent" : "tool";
}

function Legend({ kinds }: { kinds: string[] }) {
  return (
    <span className="flex items-center gap-1" title="block kinds">
      {kinds.map((kind) => (
        <span key={kind} className={`badge badge-${kind}`}>
          {kind}
        </span>
      ))}
    </span>
  );
}

function StepCard({
  step,
  kind,
  showThinking,
  showTools,
  isOpen,
  onToggle,
  onOpenSubagent,
}: {
  step: StepRow;
  kind: StepKind;
  showThinking: boolean;
  showTools: boolean;
  isOpen: (key: string) => boolean;
  onToggle: (key: string, open: boolean) => void;
  onOpenSubagent: (id: string) => void;
}) {
  return (
    <div className={`step-card kind-${kind}`}>
      <div className="step-card-head flex items-center gap-2 px-3 py-1 text-micro">
        <span className={`badge badge-${kind}`}>{kind}</span>
        <span className="num text-ink-4">#{step.stepId}</span>
        {step.model ? <span className="text-ink-4">{step.model}</span> : null}
      </div>
      <div className="space-y-1.5 px-3 py-2">
        {showThinking && step.thinking ? (
          <Collapsible header="thinking" badge="thinking">
            <div className="text-micro whitespace-pre-wrap text-ink-2">
              {step.thinking}
            </div>
          </Collapsible>
        ) : null}
        {step.text ? (
          step.source === "agent" ? (
            <Markdown text={step.text} />
          ) : (
            <div className="text-meta whitespace-pre-wrap">{step.text}</div>
          )
        ) : null}
        {showTools &&
          step.toolCalls.map((call) => (
            <div key={call.id} className={`tool-block kind-${callKind(call)}`}>
              <Collapsible
                header={
                  <span>
                    <span className="font-mono">{call.name}</span>
                    {call.isError ? (
                      <span className="text-err-text"> · error</span>
                    ) : null}
                  </span>
                }
                badge={callKind(call)}
                actions={call.subagents.map((link, i) =>
                  link.tab === null ? (
                    <span
                      key={i}
                      className="row-meta"
                      title="this trajectory does not embed the subagent's steps"
                    >
                      {link.name} · not embedded
                    </span>
                  ) : (
                    <button
                      key={i}
                      className="btn btn-sm btn-quiet"
                      title="open the subagent's trajectory"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenSubagent(link.tab!);
                      }}
                    >
                      {link.name} ›
                    </button>
                  ),
                )}
                copyText={toYaml(call.args)}
                open={isOpen(callKey(step.stepId, call.id))}
                onOpenChange={(open) =>
                  onToggle(callKey(step.stepId, call.id), open)
                }
              >
                <div className="space-y-1">
                  <PreBlock text={toYaml(call.args)} language="yaml" />
                  {call.result !== null ? (
                    <PreBlock text={call.result} language="text" />
                  ) : (
                    <div className="text-micro text-ink-4">
                      no recorded result
                    </div>
                  )}
                </div>
              </Collapsible>
            </div>
          ))}
      </div>
    </div>
  );
}

function Totals({ model }: { model: TrajectoryModel }) {
  const t = model.totals;
  return (
    <div className="num flex flex-wrap gap-3 text-micro text-ink-3">
      <span>{t.steps} steps</span>
      <span>
        {t.toolCalls} tool calls
        {t.erroredToolCalls ? (
          <span className="text-err-text"> ({t.erroredToolCalls} errored)</span>
        ) : null}
      </span>
      {t.prompt !== null ? <span>{formatTokens(t.prompt)} in</span> : null}
      {t.completion !== null ? (
        <span>{formatTokens(t.completion)} out</span>
      ) : null}
      {t.cached ? <span>{formatTokens(t.cached)} cached</span> : null}
      {t.costUsd !== null ? <span>{formatUsd(t.costUsd)}</span> : null}
      <span className="text-ink-4">{model.schemaVersion}</span>
    </div>
  );
}

export function TrajectoryViewer({ doc }: { doc: AtifTrajectory }) {
  const model = useMemo(() => toModel(doc), [doc]);
  const timing = useMemo(() => timingAnalysis(model), [model]);
  const [tab, setTab] = useState(model.tabs[0]?.id ?? "main");
  const [showThinking, setShowThinking] = useState(true);
  const [showTools, setShowTools] = useState(true);
  const [showSystem, setShowSystem] = useState(true);
  const [highlight, setHighlight] = useState<number | null>(null);
  const [openCalls, setOpenCalls] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const highlightTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const active = model.tabs.find((t) => t.id === tab) ?? model.tabs[0];
  const inSubagent = active.id !== model.tabs[0].id;

  const durations = useMemo(() => {
    const map = new Map<string, number>();
    for (const row of timing.rows) {
      if (row.duration_ms !== null)
        map.set(`${row.agent}|${row.step}`, row.duration_ms);
    }
    return map;
  }, [timing]);

  const hasBody = (step: StepRow) =>
    !!step.text.trim() ||
    (showThinking && !!step.thinking) ||
    (showTools && step.toolCalls.length > 0);

  const visible = active.steps.filter(
    (step) => (showSystem || step.source !== "system") && hasBody(step),
  );
  const hasSystem = active.steps.some((step) => step.source === "system");
  const prompts = active.steps.filter(
    (step) => step.source === "user" && step.text.trim(),
  );
  const errors = useMemo(() => errorEntries(active.steps), [active]);
  const kinds = useMemo(() => {
    const present = new Set<string>(
      active.steps.map((step) => stepKind(step, inSubagent)),
    );
    const calls = active.steps.flatMap((step) => step.toolCalls);
    if (calls.some((c) => !c.subagents.length)) present.add("tool");
    if (calls.some((c) => c.subagents.length)) present.add("subagent");
    return ["system", "user", "agent", "subagent", "tool"].filter((k) =>
      present.has(k),
    );
  }, [active, inSubagent]);
  const openTab = (id: string) => {
    setTab(id);
    setHighlight(null);
    setOpenCalls(new Set());
  };

  const toggleCall = (key: string, open: boolean) =>
    setOpenCalls((prev) => {
      const next = new Set(prev);
      if (open) next.add(key);
      else next.delete(key);
      return next;
    });

  const jumpTo = (stepId: number) => {
    const el = containerRef.current?.querySelector(
      `[data-step-id="${stepId}"]`,
    );
    el?.scrollIntoView({ behavior: "smooth", block: "start" });
    if (highlightTimer.current) clearTimeout(highlightTimer.current);
    setHighlight(stepId);
    highlightTimer.current = setTimeout(() => setHighlight(null), 1500);
  };
  const jumpToError = (entry: ErrorEntry) => {
    toggleCall(callKey(entry.stepId, entry.callId), true);
    jumpTo(entry.stepId);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Totals model={model} />
      {model.tabs.length > 1 ? (
        <div className="mt-2">
          <Tabs
            tabs={model.tabs.map((t) => ({
              id: t.id,
              label: `${t.id === model.tabs[0].id ? "" : "↳ subagent "}${t.label} (${t.steps.length})`,
            }))}
            active={active.id}
            onSelect={openTab}
          />
        </div>
      ) : null}
      <div className="mt-2 flex min-h-96 flex-1 overflow-hidden rounded-lg border border-line">
        <div
          className="min-w-0 flex-1 overflow-auto px-4 pb-8"
          ref={containerRef}
        >
          <div className="sticky top-0 z-10 -mx-4 mb-2 flex items-center gap-4 border-b border-line bg-canvas/95 px-4 py-1.5 backdrop-blur">
            {(
              [
                ["thinking", showThinking, setShowThinking],
                ["tool calls", showTools, setShowTools],
                ...(hasSystem
                  ? ([["system", showSystem, setShowSystem]] as const)
                  : []),
              ] as const
            ).map(([label, value, setter]) => (
              <label key={label} className="check">
                <input
                  type="checkbox"
                  checked={value}
                  onChange={(e) => setter(e.target.checked)}
                />
                {label}
              </label>
            ))}
            <span className="ml-auto">
              <Legend kinds={kinds} />
            </span>
            <span className="row-meta">
              {visible.length} of {active.steps.length} entries
            </span>
          </div>
          <div className="space-y-2">
            {visible.map((step) => (
              <div
                key={step.stepId}
                data-step-id={step.stepId}
                className={`flex gap-3 rounded-lg transition-colors duration-500 ${
                  highlight === step.stepId ? "bg-info-bg" : ""
                }`}
              >
                <StepGutter
                  step={step}
                  durationMs={
                    durations.get(`${active.label}|${step.stepId}`) ?? null
                  }
                />
                <div className="min-w-0 flex-1">
                  <StepCard
                    step={step}
                    kind={stepKind(step, inSubagent)}
                    onOpenSubagent={openTab}
                    showThinking={showThinking}
                    showTools={showTools}
                    isOpen={(id) => openCalls.has(id)}
                    onToggle={toggleCall}
                  />
                </div>
              </div>
            ))}
            {visible.length === 0 && (
              <div className="pane-status">
                Nothing to show with these filters.
              </div>
            )}
          </div>
        </div>
        <nav className="w-64 shrink-0 overflow-auto border-l border-line bg-surface px-2 py-2">
          <div className="eyebrow">Prompts</div>
          {prompts.map((step, i) => (
            <button
              key={step.stepId}
              className="flex w-full gap-2 rounded px-1.5 py-1 text-left text-meta text-ink-2 transition-colors hover:bg-fill"
              onClick={() => jumpTo(step.stepId)}
            >
              <span className="num shrink-0 text-ink-4">{i + 1}</span>
              <span className="line-clamp-3">{step.text.slice(0, 120)}</span>
            </button>
          ))}
          {prompts.length === 0 && (
            <div className="px-1.5 text-meta text-ink-3">
              No user prompts in this trajectory.
            </div>
          )}
          {errors.length > 0 && (
            <>
              <div className="eyebrow mt-3 text-err-text">Errors</div>
              {errors.map((e) => (
                <button
                  key={callKey(e.stepId, e.callId)}
                  className="flex w-full gap-2 rounded px-1.5 py-1 text-left text-meta text-ink-2 transition-colors hover:bg-fill"
                  onClick={() => jumpToError(e)}
                >
                  <span className="num shrink-0 text-ink-4">#{e.stepId}</span>
                  <span className="truncate font-mono">{e.tool}</span>
                </button>
              ))}
            </>
          )}
        </nav>
      </div>
    </div>
  );
}
