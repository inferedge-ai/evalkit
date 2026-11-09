import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiJson, isNotFound } from "../lib/api";
import { FORM_DIALOG, FORM_DIALOG_BODY } from "../lib/formDialog";
import { questionText } from "../lib/format";
import { useApi } from "../lib/query";
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

const NO_JUDGE: JudgeChoice = { model: "", provider: "", reasoning_effort: "" };

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
  const agentsQuery = useApi<AgentInfo[]>("/agents", {
    select: (all) =>
      serverRuns
        ? all.filter((a) => a.driver === "server")
        : [
            ...all.filter((a) => a.driver === "cli"),
            ...all.filter((a) => a.driver === "server"),
          ],
  });
  const agents = agentsQuery.data ?? [];
  const [pickedAgent, setAgent] = useState("");
  const agent = pickedAgent || agents[0]?.ref || "";
  const casesQuery = useApi<CaseView[]>(
    `/datasets/${encodeURIComponent(dataset)}/cases`,
  );
  const examples = casesQuery.data ?? null;
  const ds = useApi<Dataset>(`/datasets/${encodeURIComponent(dataset)}`).data;
  const isSuite = !!ds && ds.kind === "suite" && ds.workspace_is_shared;
  const [choice, setChoice] = useState<ScopeChoice>({
    scope: initialCaseIds?.length ? "cases" : "all",
    tags: [],
    file: "",
    caseIds: initialCaseIds ?? [],
  });
  const [runs, setRuns] = useState(1);
  const [model, setModel] = useState("");
  const [scoring, setScoring] = useState("");
  const judgeQuery = useApi<JudgeSettings>("/judge-default");
  const judgeDefault = judgeQuery.data ?? null;
  const providersQuery = useApi<LlmProviderInfo[]>("/llm-providers");
  const providers = providersQuery.data ?? [];
  const [editedJudge, setJudge] = useState<JudgeChoice | null>(null);
  const judge =
    editedJudge ?? (judgeDefault ? judgeChoice(judgeDefault) : NO_JUDGE);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const loadError =
    agentsQuery.error ??
    providersQuery.error ??
    (isNotFound(judgeQuery.error) ? null : judgeQuery.error) ??
    casesQuery.error;
  const error = actionError ?? (loadError ? String(loadError) : null);

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
    setActionError(null);
    try {
      const resp = await apiJson<{ id: number }>(
        "/experiments",
        "POST",
        request,
      );
      navigate(`/experiments/${resp.id}`);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
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
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className={FORM_DIALOG}>
        <DialogHeader>
          <DialogTitle>Run experiment</DialogTitle>
          <DialogDescription className="font-mono">{dataset}</DialogDescription>
        </DialogHeader>
        <div className={FORM_DIALOG_BODY}>
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
                        <span
                          key={tag}
                          className="chip muted inline-flex items-center font-mono"
                        >
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
                            <X size={10} />
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
                    <Button
                      variant="outline"
                      size="sm"
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
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!choice.caseIds.length}
                      onClick={() => setChoice((c) => ({ ...c, caseIds: [] }))}
                    >
                      Clear
                    </Button>
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
              value={judge.provider}
              onChange={(provider) => setJudge({ ...judge, provider })}
              options={[
                ...(judgeDefault
                  ? [{ value: "", label: "server default" }]
                  : []),
                ...providers.map((p) => ({
                  value: p.name,
                  label: `${p.name} (${p.base_url})`,
                })),
              ]}
              title="LLM providers an admin registered; judge-using evaluators run on the server with these"
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
                onChange={(e) => setJudge({ ...judge, [key]: e.target.value })}
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
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>
            Cancel
          </DialogClose>
          {serverRuns ? (
            <Button
              disabled={busy || !request || inScope.length === 0}
              onClick={launch}
            >
              {busy
                ? "Starting…"
                : `Start ${inScope.length * runs} run${inScope.length * runs === 1 ? "" : "s"}`}
            </Button>
          ) : (
            <CopyCommandButton
              text={request && inScope.length ? cliCommand(request) : null}
            />
          )}
          {/* Last in the DOM: the footer stacks in reverse on a phone, so this
              lands above the buttons there, and left of them from sm up. */}
          {error && (
            <span className="text-meta text-err-text sm:order-first sm:mr-auto sm:self-center">
              {error}
            </span>
          )}
          {!serverRuns && !error && (
            <span className="text-meta text-ink-3 sm:order-first sm:mr-auto sm:self-center">
              Datasets in a personal workspace run from your terminal.
            </span>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CopyCommandButton({ text }: { text: string | null }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
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
    </Button>
  );
}
