import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, qs } from "../lib/api";
import { EXPERIMENT_LIST_LIMIT } from "../lib/experimentStatus";
import { questionText } from "../lib/format";
import {
  SEARCH_KINDS,
  levelOne,
  rankItems,
  splitItems,
  workspaceItems,
} from "../lib/palette";
import type { LevelOneEntry, PaletteItem, SearchKind } from "../lib/palette";
import type {
  AgentInfo,
  CaseView,
  Dataset,
  ExperimentListItem,
  RunList,
} from "../lib/types";
import { useMe } from "./capabilities";
import { ADMIN_NAV, MORE_NAV, NAV } from "./nav";
import { useSwitchWorkspace, useWorkspace } from "./workspace";

interface Loaded {
  dataset: PaletteItem[];
  split: PaletteItem[];
  experiment: PaletteItem[];
  case: PaletteItem[];
}

const enc = encodeURIComponent;

async function loadLocal(workspace: string): Promise<Loaded> {
  const [datasets, experiments] = await Promise.all([
    api<Dataset[]>(`/datasets${qs({ workspace })}`),
    api<ExperimentListItem[]>(
      `/experiments${qs({ workspace, brief: "true", limit: String(EXPERIMENT_LIST_LIMIT) })}`,
    ).catch(() => []),
  ]);
  const perDataset = await Promise.all(
    datasets.map(async (ds) => ({
      ds,
      cases: await api<CaseView[]>(`/datasets/${enc(ds.ref)}/cases`).catch(
        () => [],
      ),
    })),
  );
  return {
    dataset: datasets.map((ds) => ({
      key: `dataset:${ds.ref}`,
      label: ds.ref,
      sub: ds.kind,
      tag: "dataset",
      route: `/datasets/${enc(ds.ref)}`,
      terms: [ds.ref],
    })),
    split: perDataset
      .flatMap(({ ds, cases }) => splitItems(ds.ref, cases))
      .sort(
        (a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key),
      ),
    experiment: experiments.map((exp) => ({
      key: `experiment:${exp.id}`,
      label: `${exp.dataset} #${exp.number}`,
      sub: `${exp.agent} · experiment ${exp.id}`,
      tag: "experiment",
      route: `/experiments/${exp.id}`,
      terms: [String(exp.id), `#${exp.number}`, exp.dataset],
    })),
    case: perDataset.flatMap(({ ds, cases }) =>
      cases.map((c) => ({
        key: `case:${ds.ref}:${c.case_id}`,
        label: c.case_id,
        sub: `${ds.ref} · ${questionText(c.input)}`,
        tag: "case",
        route: `/datasets/${enc(ds.ref)}/cases/${enc(c.case_id)}`,
        terms: [c.case_id, questionText(c.input)],
      })),
    ),
  };
}

async function searchRuns(
  workspace: string,
  query: string,
): Promise<PaletteItem[]> {
  const agents = await api<AgentInfo[]>(`/agents${qs({ workspace })}`);
  const lists = await Promise.all(
    agents.map((a) =>
      api<RunList>(
        `/agents/${enc(a.ref)}/runs${qs({ query: query || undefined, limit: "10" })}`,
      )
        .then((list) => ({ agent: a.ref, runs: list.runs }))
        .catch(() => ({ agent: a.ref, runs: [] })),
    ),
  );
  return lists.flatMap(({ agent, runs }) =>
    runs.map((run) => ({
      key: `run:${agent}:${run.run_id}`,
      label: run.title || run.run_id,
      sub: `${agent} · ${run.run_id}`,
      tag: "run",
      route: `/runs/${enc(agent)}/${enc(run.run_id)}`,
      terms: [run.run_id, run.title ?? ""],
    })),
  );
}

type Row = LevelOneEntry;

export function JumpPalette() {
  const me = useMe();
  const { workspace, workspaces } = useWorkspace();
  const switchWorkspace = useSwitchWorkspace();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<SearchKind | null>(null);
  const [value, setValue] = useState("");
  const [hi, setHi] = useState(0);
  const [local, setLocal] = useState<Loaded | null>(null);
  const [runs, setRuns] = useState<PaletteItem[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const navigate = useNavigate();
  const pages = useMemo(
    () => [...NAV, ...MORE_NAV, ...(me.is_admin ? ADMIN_NAV : [])],
    [me.is_admin],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key === "Escape") setOpen(false);
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("jump-palette:open", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("jump-palette:open", onOpen);
    };
  }, []);

  useEffect(() => {
    if (!open) {
      setKind(null);
      setValue("");
      return;
    }
    inputRef.current?.focus();
    setLocal(null);
    loadLocal(workspace.name)
      .then(setLocal)
      .catch(() => {});
  }, [open, workspace.name]);

  useEffect(() => {
    if (!open || kind !== "run") return;
    setRuns(null);
    const handle = setTimeout(() => {
      searchRuns(workspace.name, value.trim())
        .then(setRuns)
        .catch(() => setRuns([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [open, kind, value, workspace.name]);

  useEffect(() => setHi(0), [value, open, kind]);

  const spaces = useMemo(
    () => workspaceItems(workspaces, workspace.name, me.personal_workspace),
    [workspaces, workspace.name, me.personal_workspace],
  );

  const allLocal = useMemo(
    () => [
      ...spaces,
      ...(local
        ? [...local.dataset, ...local.split, ...local.experiment, ...local.case]
        : []),
    ],
    [spaces, local],
  );

  const rows = useMemo((): Row[] => {
    if (kind === null) return levelOne(value, pages, allLocal);
    const pool =
      kind === "workspace"
        ? spaces
        : kind === "run"
          ? (runs ?? [])
          : local
            ? local[kind]
            : [];
    return (kind === "run" ? pool : rankItems(pool, value)).map((item) => ({
      type: "item",
      item,
    }));
  }, [kind, value, pages, allLocal, local, runs, spaces]);

  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[data-idx="${hi}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [hi, rows.length]);

  const go = (route: string, newTab = false) => {
    setOpen(false);
    if (newTab) window.open(route, "_blank", "noopener");
    else navigate(route);
  };

  const enterKind = (k: SearchKind) => {
    setKind(k);
    setValue("");
    inputRef.current?.focus();
  };

  const choose = (row: Row | undefined, newTab: boolean) => {
    if (!row) return;
    if (row.type === "kind") enterKind(row.kind);
    else if (row.type === "page") go(row.route, newTab);
    else if (row.item.switchTo) {
      setOpen(false);
      switchWorkspace(row.item.switchTo);
    } else go(row.item.route, newTab);
  };

  if (!open) return null;
  const active = SEARCH_KINDS.find((k) => k.kind === kind);
  const loading =
    kind !== null &&
    kind !== "workspace" &&
    (kind === "run" ? runs === null : local === null);

  return (
    <div className="dialog-backdrop" onMouseDown={() => setOpen(false)}>
      <div
        className="dialog w-140"
        role="dialog"
        aria-label="Search"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-line px-3">
          {active && (
            <button
              className="chip shrink-0"
              title="back to all types (Backspace)"
              onClick={() => {
                setKind(null);
                inputRef.current?.focus();
              }}
            >
              {active.label} ›
            </button>
          )}
          <input
            ref={inputRef}
            className="palette-input min-w-0 flex-1 bg-transparent py-2.5 text-body outline-none"
            placeholder={
              active
                ? `Search ${active.label.toLowerCase()} by ${active.hint}`
                : "Pick a type, go to a page, or type an id"
            }
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setHi((h) => Math.min(h + 1, rows.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setHi((h) => Math.max(h - 1, 0));
              } else if (e.key === "Tab" && kind === null) {
                const row = rows[hi];
                if (row?.type === "kind") {
                  e.preventDefault();
                  enterKind(row.kind);
                }
              } else if (e.key === "Backspace" && kind !== null && !value) {
                e.preventDefault();
                setKind(null);
              } else if (e.key === "Enter") {
                e.preventDefault();
                const newTab = e.metaKey || e.ctrlKey;
                if (rows[hi]) choose(rows[hi], newTab);
                else if (
                  value.trim() &&
                  (kind === null || kind === "experiment")
                )
                  go(`/experiments/${enc(value.trim())}`, newTab);
              }
            }}
          />
        </div>
        <ul className="max-h-80 overflow-auto p-1" ref={listRef}>
          {loading && <li className="select-empty">Searching…</li>}
          {!loading && rows.length === 0 && (
            <li className="select-empty">
              {value.trim() ? "No matches" : "Nothing here yet"}
            </li>
          )}
          {rows.map((row, i) => (
            <li key={rowKey(row)}>
              <button
                data-idx={i}
                className={`select-option w-full text-left ${i === hi ? "active" : ""}`}
                onMouseEnter={() => setHi(i)}
                onClick={(e) => choose(row, e.metaKey || e.ctrlKey)}
              >
                <RowBody row={row} />
              </button>
            </li>
          ))}
        </ul>
        <div className="border-t border-line px-3 py-1.5 text-micro text-ink-4">
          {kind === null
            ? "↑↓ choose · Enter or Tab opens a type · Enter on an unknown id opens it as an experiment"
            : "↑↓ choose · Enter opens · ⌘Enter new tab · Backspace on empty goes back"}
        </div>
      </div>
    </div>
  );
}

function rowKey(row: Row): string {
  if (row.type === "kind") return `kind:${row.kind}`;
  if (row.type === "page") return `page:${row.route}`;
  return row.item.key;
}

function RowBody({ row }: { row: Row }) {
  if (row.type === "kind")
    return (
      <>
        <span className="font-medium text-ink">{row.label}</span>
        <span className="truncate text-ink-4">{row.hint}</span>
        <span className="select-hint">›</span>
      </>
    );
  if (row.type === "page")
    return (
      <>
        <span className="text-ink">Go to {row.label}</span>
        <span className="select-hint">page</span>
      </>
    );
  return (
    <>
      <span className="shrink-0 truncate font-mono text-ink">
        {row.item.label}
      </span>
      {row.item.sub && (
        <span className="min-w-0 truncate text-ink-4">{row.item.sub}</span>
      )}
      <span className="select-hint">{row.item.tag}</span>
    </>
  );
}
