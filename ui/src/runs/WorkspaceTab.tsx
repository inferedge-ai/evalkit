import { useEffect, useMemo, useRef, useState } from "react";
import { Editor } from "../lib/Editor";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, apiBlobUrl, apiFetch, downloadFile, qs } from "../lib/api";
import { formatBytes } from "../lib/format";
import { monacoTheme, useTheme } from "../shell/theme";
import type { FileEntry } from "../lib/types";
import { CopyButton } from "../lib/ui";

interface FileNode {
  name: string;
  path: string;
  type: "dir" | "file";
  size?: number;
  children?: FileNode[];
}

const IMAGE_EXTS = new Set([
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "bmp",
  "ico",
  "avif",
]);
const PREVIEW_EXTS = new Set(["md", "html", "htm", "svg"]);
const MAX_INLINE_BYTES = 4 * 1024 * 1024;

const LANG_BY_EXT: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  json: "json",
  jsonl: "json",
  md: "markdown",
  html: "html",
  htm: "html",
  svg: "xml",
  xml: "xml",
  css: "css",
  yml: "yaml",
  yaml: "yaml",
  toml: "ini",
  ini: "ini",
  sh: "shell",
  bash: "shell",
  sql: "sql",
  csv: "plaintext",
};

function encodePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

function describeDownloadError(e: unknown): string {
  return `download failed: ${e instanceof Error ? e.message : String(e)}`;
}

function ext(name: string): string {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

function buildTree(files: FileEntry[]): FileNode[] {
  const root: FileNode[] = [];
  const dirs = new Map<string, FileNode>();
  const dirFor = (path: string): FileNode[] => {
    if (!path) return root;
    const existing = dirs.get(path);
    if (existing) return existing.children!;
    const slash = path.lastIndexOf("/");
    const node: FileNode = {
      name: slash === -1 ? path : path.slice(slash + 1),
      path,
      type: "dir",
      children: [],
    };
    dirs.set(path, node);
    dirFor(slash === -1 ? "" : path.slice(0, slash)).push(node);
    return node.children!;
  };
  for (const file of files) {
    const slash = file.path.lastIndexOf("/");
    dirFor(slash === -1 ? "" : file.path.slice(0, slash)).push({
      name: slash === -1 ? file.path : file.path.slice(slash + 1),
      path: file.path,
      type: "file",
      size: file.size,
    });
  }
  const sortNodes = (nodes: FileNode[]) => {
    nodes.sort(
      (a, b) =>
        Number(b.type === "dir") - Number(a.type === "dir") ||
        a.name.localeCompare(b.name),
    );
    for (const node of nodes) if (node.children) sortNodes(node.children);
  };
  sortNodes(root);
  return root;
}

// A workspace can hold hundreds of files; filtering keeps the matching
// branches and drops the rest.
function filterTree(nodes: FileNode[], needle: string): FileNode[] {
  if (!needle) return nodes;
  const out: FileNode[] = [];
  for (const node of nodes) {
    if (node.type === "dir") {
      const children = filterTree(node.children ?? [], needle);
      if (children.length) out.push({ ...node, children });
      else if (node.name.toLowerCase().includes(needle)) out.push(node);
    } else if (node.name.toLowerCase().includes(needle)) {
      out.push(node);
    }
  }
  return out;
}

function TreeNode({
  base,
  node,
  depth,
  selected,
  onSelect,
  onError,
  forceOpen,
}: {
  base: string;
  node: FileNode;
  depth: number;
  selected: string | null;
  onSelect: (node: FileNode) => void;
  onError: (message: string) => void;
  forceOpen: boolean;
}) {
  const [expanded, setExpanded] = useState(depth === 0);
  const open = expanded || forceOpen;
  const pad = { paddingLeft: `${depth * 14 + 6}px` };
  if (node.type === "dir") {
    return (
      <div>
        <div
          className="group flex cursor-pointer items-center gap-1 py-0.5 pr-2 text-meta font-medium text-ink hover:bg-fill"
          style={pad}
          onClick={() => setExpanded(!open)}
        >
          <span className="text-ink-4">{open ? "▾" : "▸"}</span>
          <span>{node.name}/</span>
          <button
            className="ml-auto hidden text-micro text-ink-3 group-hover:inline hover:text-brand"
            title="download folder as zip"
            onClick={(e) => {
              e.stopPropagation();
              downloadFile(`${base}/zip${qs({ path: node.path })}`).catch(
                (err) => onError(describeDownloadError(err)),
              );
            }}
          >
            ⇩ zip
          </button>
        </div>
        {open &&
          node.children?.map((child) => (
            <TreeNode
              key={child.path}
              base={base}
              node={child}
              depth={depth + 1}
              selected={selected}
              onSelect={onSelect}
              onError={onError}
              forceOpen={forceOpen}
            />
          ))}
      </div>
    );
  }
  return (
    <button
      className={`flex w-full items-center gap-2 py-0.5 pr-2 text-left text-meta transition-colors ${
        selected === node.path
          ? "bg-info-bg text-brand"
          : "text-ink-2 hover:bg-fill"
      }`}
      style={pad}
      onClick={() => onSelect(node)}
      title={node.path}
    >
      <span className="truncate font-mono">{node.name}</span>
      <span className="num ml-auto shrink-0 text-micro text-ink-4">
        {formatBytes(node.size ?? 0)}
      </span>
    </button>
  );
}

// Object-URL for an API-served file, fetched through the authed client (a
// plain <img src>/<iframe src> would 401 without the bearer token).
function useBlobUrl(path: string | null): {
  url: string | null;
  error: string | null;
} {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setUrl(null);
    setError(null);
    if (!path) return;
    let revoked: string | null = null;
    let cancelled = false;
    apiBlobUrl(path)
      .then((u) => {
        if (cancelled) {
          URL.revokeObjectURL(u);
          return;
        }
        revoked = u;
        setUrl(u);
      })
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [path]);
  return { url, error };
}

function FileViewer({ base, node }: { base: string; node: FileNode }) {
  const { theme } = useTheme();
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const extension = ext(node.name);
  const hasPreview = PREVIEW_EXTS.has(extension);
  const [view, setView] = useState<"preview" | "source">(
    hasPreview ? "preview" : "source",
  );
  const path = `${base}/files/${encodePath(node.path)}`;
  const isImage = IMAGE_EXTS.has(extension);
  const isPdf = extension === "pdf";
  const tooLarge = (node.size ?? 0) > MAX_INLINE_BYTES;

  // Binary/embedded previews (img/iframe) need object URLs.
  const wantsBlob =
    isImage ||
    isPdf ||
    (hasPreview &&
      view === "preview" &&
      (extension === "svg" || extension === "html" || extension === "htm") &&
      !tooLarge);
  const blob = useBlobUrl(wantsBlob ? path : null);

  useEffect(() => {
    setText(null);
    setError(null);
    setView(PREVIEW_EXTS.has(ext(node.name)) ? "preview" : "source");
    if (isImage || isPdf || tooLarge) return;
    apiFetch(path)
      .then(async (resp) => {
        if (resp.status === 404) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(
            body.detail ===
              "not found on the harness: wrong path, or the harness no longer has it"
              ? "The harness doesn't have this file: the path is wrong, or the harness has dropped the session. Only stored deliverables outlive the session."
              : "No such file.",
          );
        }
        if (!resp.ok)
          throw new Error(`Failed to load file: ${resp.statusText}`);
        const raw = await resp.text();
        if (ext(node.name) === "json") {
          try {
            setText(JSON.stringify(JSON.parse(raw), null, 2));
            return;
          } catch {
            // fall through to raw text
          }
        }
        setText(raw);
      })
      .catch((e) => setError(e.message));
  }, [path, isImage, isPdf, tooLarge, node.name]);

  const showSource = !isImage && !isPdf && (!hasPreview || view === "source");
  const showPreview = !isImage && hasPreview && view === "preview" && !tooLarge;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-3 border-b border-line bg-surface px-3 py-1.5 text-meta">
        <span className="truncate font-mono">{node.path}</span>
        <span className="num shrink-0 text-ink-3">
          {formatBytes(node.size ?? 0)}
        </span>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {hasPreview && !tooLarge && (
            <div className="segment">
              {(["preview", "source"] as const).map((v) => (
                <button
                  key={v}
                  aria-pressed={view === v}
                  onClick={() => setView(v)}
                >
                  {v}
                </button>
              ))}
            </div>
          )}
          {text !== null && <CopyButton text={text} />}
          <button
            className="btn btn-sm"
            onClick={() =>
              downloadFile(`${path}${qs({ download: "1" })}`).catch((e) =>
                setError(describeDownloadError(e)),
              )
            }
          >
            Download
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {(error || blob.error) && (
          <div className="pane-status error">{error ?? blob.error}</div>
        )}
        {isImage && blob.url && (
          <img src={blob.url} className="max-h-full max-w-full p-4" />
        )}
        {isPdf && blob.url && (
          <iframe
            className="h-full w-full border-0"
            src={blob.url}
            title="pdf"
          />
        )}
        {tooLarge && showSource && (
          <div className="pane-status">
            File is too large to display inline — use Download.
          </div>
        )}
        {showPreview &&
          (extension === "md" ? (
            <div className="md-body max-w-3xl p-4 text-meta">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {text ?? ""}
              </ReactMarkdown>
            </div>
          ) : extension === "svg" ? (
            blob.url && (
              <img src={blob.url} className="max-h-full max-w-full p-4" />
            )
          ) : (
            blob.url && (
              <iframe
                className="h-full w-full border-0"
                sandbox="allow-scripts"
                src={blob.url}
                title="preview"
              />
            )
          ))}
        {showSource && !tooLarge && text === null && !error && (
          <div className="pane-status">Loading…</div>
        )}
        {showSource && text !== null && (
          <Editor
            height="100%"
            theme={monacoTheme(theme)}
            language={LANG_BY_EXT[extension] ?? "plaintext"}
            value={text}
            options={{
              readOnly: true,
              minimap: { enabled: false },
              fontSize: 12,
              wordWrap:
                extension === "md" || extension === "txt" ? "on" : "off",
              scrollBeyondLastLine: false,
              renderLineHighlight: "none",
            }}
          />
        )}
      </div>
    </div>
  );
}

const TREE_WIDTH_KEY = "evalkit.workspace.treeWidth";
const MIN_TREE_WIDTH = 180;
const MAX_TREE_SHARE = 0.6;

export function WorkspaceTab({
  agent,
  runId,
}: {
  agent: string;
  runId: string;
}) {
  const [files, setFiles] = useState<FileEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<FileNode | null>(null);
  const [filter, setFilter] = useState("");
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [treeWidth, setTreeWidth] = useState(() => {
    const saved = Number(localStorage.getItem(TREE_WIDTH_KEY));
    return Number.isFinite(saved) && saved >= MIN_TREE_WIDTH ? saved : 288;
  });

  const dragTree = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
    const box = containerRef.current?.getBoundingClientRect();
    if (!box) return;
    setTreeWidth(
      Math.min(
        Math.max(e.clientX - box.left, MIN_TREE_WIDTH),
        box.width * MAX_TREE_SHARE,
      ),
    );
  };

  const base = `/agents/${encodeURIComponent(agent)}/runs/${encodeURIComponent(runId)}`;

  useEffect(() => {
    setFiles(null);
    setSelected(null);
    api<{ files: FileEntry[] }>(`${base}/files`)
      .then((r) => setFiles(r.files))
      .catch((e) =>
        setError(
          `${e instanceof Error ? e.message : e} — the harness may have pruned this session's workspace`,
        ),
      );
  }, [base]);

  const tree = useMemo(() => buildTree(files ?? []), [files]);

  if (error) return <div className="pane-status error">{error}</div>;
  if (!files)
    return (
      <div className="space-y-1.5 p-2">
        {Array.from({ length: 8 }, (_, i) => (
          <div
            key={i}
            className="skeleton h-4"
            style={{ width: `${20 + i * 7}%` }}
          />
        ))}
      </div>
    );
  if (files.length === 0)
    return <div className="pane-status">Workspace is empty.</div>;

  const needle = filter.trim().toLowerCase();
  const visible = filterTree(tree, needle);

  return (
    <div
      ref={containerRef}
      className="flex h-[calc(100dvh-16rem)] min-h-96 overflow-hidden rounded-lg border border-line"
    >
      <div
        className="flex shrink-0 flex-col border-r border-line bg-surface"
        style={{ width: treeWidth }}
      >
        <div className="flex items-center gap-2 border-b border-line-soft p-2">
          <input
            className="input btn-sm min-w-0 flex-1"
            placeholder="Filter files"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <button
            className="btn btn-sm btn-quiet shrink-0"
            title="download the whole workspace as zip"
            onClick={() =>
              downloadFile(`${base}/zip`).catch((e) =>
                setDownloadError(describeDownloadError(e)),
              )
            }
          >
            ⇩ zip all
          </button>
        </div>
        {downloadError && (
          <div
            className="pane-status error cursor-pointer"
            title="dismiss"
            onClick={() => setDownloadError(null)}
          >
            {downloadError}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-auto py-1">
          {visible.map((node) => (
            <TreeNode
              key={node.path}
              base={base}
              node={node}
              depth={0}
              selected={selected?.path ?? null}
              onSelect={setSelected}
              onError={setDownloadError}
              forceOpen={!!needle}
            />
          ))}
          {needle && visible.length === 0 && (
            <div className="px-3 py-2 text-meta text-ink-3">
              No files match.
            </div>
          )}
        </div>
      </div>
      <div
        className="w-1.5 shrink-0 cursor-col-resize touch-none bg-surface transition-colors hover:bg-brand-ring"
        role="separator"
        aria-orientation="vertical"
        aria-label="resize file tree"
        onPointerDown={(e) => {
          e.preventDefault();
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={dragTree}
        onPointerUp={(e) => {
          e.currentTarget.releasePointerCapture(e.pointerId);
          setTreeWidth((w) => {
            localStorage.setItem(TREE_WIDTH_KEY, String(Math.round(w)));
            return w;
          });
        }}
      />
      <div className="min-w-0 flex-1 bg-surface">
        {selected ? (
          <FileViewer key={selected.path} base={base} node={selected} />
        ) : (
          <div className="pane-empty">
            <div>Pick a file from the tree to view it.</div>
            <div className="text-micro text-ink-4">
              Markdown, HTML, SVG and images render inline.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
