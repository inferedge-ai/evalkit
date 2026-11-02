import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, apiJson, NotFoundError } from "../lib/api";
import { questionText } from "../lib/format";
import { stateClass, stateLabel } from "../lib/review";
import {
  casesInScope,
  cliCommand,
  selectionFor,
  sourceFiles,
} from "../lib/runLauncher";
import type { LaunchRequest, Scope, ScopeChoice } from "../lib/runLauncher";
import { MultiSelect, Select } from "../lib/Select";
import { sortedTags } from "../lib/tags";
import { judgeChoice, judgeOverride } from "../lib/judge";
import type { JudgeChoice } from "../lib/judge";
import type {
  AgentInfo,
  Dataset,
  JudgeSettings,
  LlmProviderInfo,
  TagInfo,
  CaseView,
} from "../lib/types";
import { CopyButton } from "../lib/ui";

const SCOPES: { id: Scope; label: string }[] = [
  { id: "all", label: "All cases" },
  { id: "tags", label: "Tags" },
  { id: "file", label: "Case file" },
  { id: "cases", label: "Pick cases" },
];

export function RunLauncher({
  dataset,
  tags,
  initialCaseIds,
  serverRuns,
  onClose,
}: {
  dataset: string;
  serverRuns: boolean;
  tags: TagInfo[];
  initialCaseIds?: string[];
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agent, setAgent] = useState("");
  const [examples, setExamples] = useState<CaseView[] | null>(null);
  const [isSuite, setIsSuite] = useState(false);
  const [choice, setChoice] = useState<ScopeChoice>({
    scope: initialCaseIds?.length ? "cases" : "all",
    tags: [],
    file: "",
    caseIds: initialCaseIds ?? [],
  });
  const [runs, setRuns] = useState(1);
  const [model, setModel] = useState("");
  const [scoring, setScoring] = useState("");
  const [judgeDefault, setJudgeDefault] = useState<JudgeSettings | null>(null);
  const [providers, setProviders] = useState<LlmProviderInfo[]>([]);
  const [judge, setJudge] = useState<JudgeChoice>({
    model: "",
    base_url: "",
    reasoning_effort: "",
  });
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AgentInfo[]>("/agents")
      .then((all) => {
        const list = serverRuns
          ? all.filter((a) => a.driver === "server")
          : [
              ...all.filter((a) => a.driver === "cli"),
              ...all.filter((a) => a.driver === "server"),
            ];
        setAgents(list);
        setAgent((a) => a || list[0]?.ref || "");
      })
      .catch((e) => setError(String(e)));
    api<LlmProviderInfo[]>("/llm-providers")
      .then(setProviders)
      .catch((e) => setError(String(e)));
    api<JudgeSettings>("/judge-default")
      .then((settings) => {
        setJudgeDefault(settings);
        setJudge(judgeChoice(settings));
      })
      .catch((e) => {
        setJudgeDefault(null);
        if (!(e instanceof NotFoundError)) setError(String(e));
      });
    api<CaseView[]>(`/datasets/${encodeURIComponent(dataset)}/cases`)
      .then(setExamples)
      .catch((e) => setError(String(e)));
    api<Dataset>(`/datasets/${encodeURIComponent(dataset)}`)
      .then((d) => setIsSuite(d.kind === "suite" && d.workspace_is_shared))
      .catch(() => setIsSuite(false));
  }, [dataset, serverRuns]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const files = useMemo(() => sourceFiles(examples ?? []), [examples]);
  useEffect(() => {
    if (!choice.file && files.length)
      setChoice((c) => ({ ...c, file: files[0].name }));
  }, [files, choice.file]);

  const inScope = casesInScope(examples ?? [], choice);
  const selection = selectionFor(examples ?? [], choice);
  const judgeReady =
    judgeDefault !== null || judgeOverride(null, judge) !== null;
  const request: LaunchRequest | null =
    selection && (agent || !serverRuns) && judgeReady
      ? {
          dataset,
          agent: agent || "<agent>",
          repetitions: runs,
          selection,
          model: model.trim() || null,
          judge: judgeOverride(judgeDefault, judge),
          evaluation_concurrency: Number(scoring) || null,
        }
      : null;

  const launch = async () => {
    if (!request) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await apiJson<{ id: number }>(
        "/experiments",
        "POST",
        request,
      );
      navigate(`/experiments/${resp.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const setScope = (scope: Scope) => setChoice((c) => ({ ...c, scope }));
  const picked = new Set(choice.caseIds);
  const togglePick = (caseId: string) =>
    setChoice((c) => ({
      ...c,
      caseIds: picked.has(caseId)
        ? c.caseIds.filter((id) => id !== caseId)
        : [...c.caseIds, caseId],
    }));
  const needle = filter.trim().toLowerCase();
  const pickable = (examples ?? []).filter(
    (ex) =>
      !needle ||
      ex.case_id.toLowerCase().includes(needle) ||
      questionText(ex.input).toLowerCase().includes(needle),
  );

  return (
    <div className="dialog-backdrop" onMouseDown={onClose}>
      <div
        className="dialog w-160"
        role="dialog"
        aria-label="Run experiment"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="dialog-head">
          <span className="font-semibold">Run experiment</span>
          <span className="row-meta font-mono">{dataset}</span>
          <button
            className="btn btn-quiet btn-sm ml-auto"
            aria-label="close"
            onClick={onClose}
          >
            ✕
          </button>
        </div>
        <div className="dialog-body">
          <div className="form-row">
            <span className="form-label">Agent</span>
            <Select
              ariaLabel="agent"
              value={agent}
              onChange={setAgent}
              placeholder={serverRuns ? "No shared agents" : "No agents"}
              options={agents.map((a) => ({ value: a.ref, label: a.ref }))}
            />
          </div>
          <div className="form-row items-start">
            <span className="form-label pt-1.5">Cases</span>
            <div className="flex min-w-0 flex-1 flex-col gap-2">
              <div className="segment self-start">
                {SCOPES.map((s) => (
                  <button
                    key={s.id}
                    aria-pressed={choice.scope === s.id}
                    disabled={
                      (s.id === "tags" && !tags.length) ||
                      (s.id === "file" && !files.length)
                    }
                    onClick={() => setScope(s.id)}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
              {choice.scope === "tags" && (
                <>
                  <MultiSelect
                    ariaLabel="tags"
                    className="self-start"
                    placeholder="Pick tags"
                    values={choice.tags}
                    onChange={(picked) =>
                      setChoice((c) => ({ ...c, tags: sortedTags(picked) }))
                    }
                    options={tags.map((t) => ({
                      value: t.name,
                      label: t.name,
                      hint: t.count,
                    }))}
                  />
                  {choice.tags.length > 0 && (
                    <div
                      className="flex flex-wrap gap-1.5"
                      aria-label="selected tags"
                    >
                      {choice.tags.map((tag) => (
                        <span key={tag} className="chip muted font-mono">
                          {tag}
                          <button
                            type="button"
                            className="ml-1 text-ink-3 hover:text-ink"
                            aria-label={`remove ${tag}`}
                            onClick={() =>
                              setChoice((c) => ({
                                ...c,
                                tags: c.tags.filter((t) => t !== tag),
                              }))
                            }
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </>
              )}
              {choice.scope === "file" && (
                <Select
                  ariaLabel="case file"
                  className="self-start"
                  value={choice.file}
                  onChange={(file) => setChoice((c) => ({ ...c, file }))}
                  options={files.map((f) => ({
                    value: f.name,
                    label: f.name,
                    hint: f.count,
                  }))}
                />
              )}
              {choice.scope === "cases" && (
                <div className="picker">
                  <div className="picker-toolbar">
                    <input
                      className="input h-7 min-w-0 flex-1"
                      placeholder="Filter by id or question"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    />
                    <button
                      className="btn btn-sm"
                      disabled={!pickable.length}
                      onClick={() =>
                        setChoice((c) => ({
                          ...c,
                          caseIds: [
                            ...new Set([
                              ...c.caseIds,
                              ...pickable.map((ex) => ex.case_id),
                            ]),
                          ],
                        }))
                      }
                    >
                      Select shown
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={!choice.caseIds.length}
                      onClick={() => setChoice((c) => ({ ...c, caseIds: [] }))}
                    >
                      Clear
                    </button>
                  </div>
                  <div className="picker-list">
                    {!examples && <div className="pane-status">Loading…</div>}
                    {pickable.map((ex) => (
                      <label key={ex.case_id} className="picker-row check">
                        <input
                          type="checkbox"
                          checked={picked.has(ex.case_id)}
                          onChange={() => togglePick(ex.case_id)}
                        />
                        <span className="max-w-[60%] shrink-0 truncate font-mono text-ink">
                          {ex.case_id}
                        </span>
                        <span className="truncate text-ink-3">
                          {questionText(ex.input)}
                        </span>
                        {isSuite &&
                          ex.review &&
                          ex.review.state !== "approved" && (
                            <span
                              className={`chip ml-auto shrink-0 ${stateClass(ex.review.state)}`}
                            >
                              {stateLabel(ex.review.state)}
                            </span>
                          )}
                      </label>
                    ))}
                    {examples && !pickable.length && (
                      <div className="pane-status">No cases match.</div>
                    )}
                  </div>
                </div>
              )}
              <span className="row-meta">
                {examples === null
                  ? "Loading cases…"
                  : `${inScope.length} case${inScope.length === 1 ? "" : "s"}`}
              </span>
            </div>
          </div>
          <div className="form-row">
            <span className="form-label">Repetitions</span>
            <input
              className="input w-20"
              type="number"
              min={1}
              max={10}
              value={runs}
              onChange={(e) =>
                setRuns(Math.max(1, Math.min(10, Number(e.target.value) || 1)))
              }
            />
          </div>
          <div className="form-row">
            <span className="form-label">Model</span>
            <input
              className="input w-72"
              placeholder="harness default"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              title="Used only if the harness accepts a model override"
            />
          </div>
          <div className="form-row">
            <span className="form-label">Evaluation concurrency</span>
            <input
              className="input w-20"
              type="number"
              min={1}
              placeholder="default"
              value={scoring}
              onChange={(e) =>
                setScoring(
                  e.target.value === ""
                    ? ""
                    : String(Math.max(1, Math.floor(Number(e.target.value)))),
                )
              }
              title="How many runs are scored at once. Server runs default to the server's limit and may not go above it"
            />
          </div>
          <div className="form-row">
            <span className="form-label">Judge provider</span>
            <Select
              ariaLabel="judge provider"
              value={judge.base_url}
              onChange={(base_url) => setJudge((j) => ({ ...j, base_url }))}
              options={[
                ...(judgeDefault
                  ? [{ value: "", label: "server default" }]
                  : []),
                ...providers.map((p) => ({
                  value: p.base_url,
                  label: `${p.name} (${p.base_url})`,
                })),
              ]}
              title="LLM providers an admin registered; server runs only judge with these"
            />
          </div>
          {!judgeDefault && (
            <p className="row-meta whitespace-normal">
              This server has no default judge yet. Pick a provider and a judge
              model.
            </p>
          )}
          {(["model", "reasoning_effort"] as const).map((key) => (
            <div key={key} className="form-row">
              <span className="form-label">
                {key === "model" ? "Judge model" : "Judge effort"}
              </span>
              <input
                className="input w-72"
                placeholder={judgeDefault?.[key] ?? ""}
                value={judge[key]}
                onChange={(e) =>
                  setJudge((j) => ({ ...j, [key]: e.target.value }))
                }
                title="Defaults to the server's judge; the experiment records the judge it used"
              />
            </div>
          ))}
          {request && (
            <div className="form-row items-start">
              <span className="form-label pt-1">CLI</span>
              <div className="cli-preview">
                <code>{cliCommand(request)}</code>
                <CopyButton text={cliCommand(request)} />
              </div>
            </div>
          )}
        </div>
        <div className="dialog-foot">
          {error && <span className="text-meta text-err-text">{error}</span>}
          {!serverRuns && !error && (
            <span className="text-meta text-ink-3">
              Datasets in a personal workspace run from your terminal.
            </span>
          )}
          <button className="btn btn-quiet ml-auto" onClick={onClose}>
            Cancel
          </button>
          {serverRuns ? (
            <button
              className="btn btn-primary"
              disabled={busy || !request || inScope.length === 0}
              onClick={launch}
            >
              {busy
                ? "Starting…"
                : `Start ${inScope.length * runs} run${inScope.length * runs === 1 ? "" : "s"}`}
            </button>
          ) : (
            <CopyCommandButton
              text={request && inScope.length ? cliCommand(request) : null}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function CopyCommandButton({ text }: { text: string | null }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn btn-primary"
      disabled={!text}
      onClick={() => {
        if (!text) return;
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? "Copied" : "Copy CLI command"}
    </button>
  );
}
