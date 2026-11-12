// The authoring markers are documented in CONTRIBUTING.md; docs/site/markers.py
// applies the same rules for the MkDocs site.
const TABS_OPEN = "<!-- tabs -->";
const TABS_CLOSE = "<!-- /tabs -->";
const ANCHOR_ROWS = "<!-- anchor-rows -->";
const ADMONITION_START = /^!!!\s+([\w-]+)(?:\s+"(.*)")?\s*$/;
const ADMONITION_OPEN = /^<!-- admonition (\S+) (\S+) "(.*)" -->$/;
const ADMONITION_CLOSE = "<!-- /admonition -->";
const FENCE = /^\s*(`{3,}|~{3,})/;

export type AdmonitionKind = "note" | "info" | "tip" | "warning" | "danger";

const ADMONITION_KINDS: Record<string, AdmonitionKind> = {
  note: "note",
  info: "info",
  todo: "info",
  abstract: "info",
  summary: "info",
  tldr: "info",
  question: "info",
  help: "info",
  faq: "info",
  example: "info",
  quote: "info",
  cite: "info",
  tip: "tip",
  hint: "tip",
  important: "tip",
  success: "tip",
  check: "tip",
  done: "tip",
  warning: "warning",
  caution: "warning",
  attention: "warning",
  danger: "danger",
  error: "danger",
  failure: "danger",
  fail: "danger",
  missing: "danger",
  bug: "danger",
};

export function readAdmonitionKind(type: string): AdmonitionKind {
  return ADMONITION_KINDS[type.toLowerCase()] ?? "note";
}

function toggleFence(line: string, fence: string | null): string | null {
  const match = FENCE.exec(line);
  if (!match) return fence;
  if (fence === null) return match[1];
  return match[1].startsWith(fence) ? null : fence;
}

function isIndented(line: string): boolean {
  return line.startsWith("    ") || line.startsWith("\t");
}

function dedent(line: string): string {
  return line.startsWith("\t") ? line.slice(1) : line.slice(4);
}

export function prepareDocMarkdown(markdown: string): string {
  const lines = markdown.split("\n");
  const out: string[] = [];
  let fence: string | null = null;
  for (let i = 0; i < lines.length; i++) {
    const start = fence === null ? ADMONITION_START.exec(lines[i]) : null;
    if (!start) {
      fence = toggleFence(lines[i], fence);
      out.push(lines[i]);
      continue;
    }
    const body: string[] = [];
    while (
      i + 1 < lines.length &&
      (lines[i + 1].trim() === "" || isIndented(lines[i + 1]))
    ) {
      body.push(dedent(lines[++i]));
    }
    while (body.length && body.at(-1)?.trim() === "") body.pop();
    const [, type, title] = start;
    const shown =
      title ?? type.charAt(0).toUpperCase() + type.slice(1).toLowerCase();
    out.push(
      "",
      `<!-- admonition ${type} ${readAdmonitionKind(type)} "${shown}" -->`,
      "",
      prepareDocMarkdown(body.join("\n")),
      "",
      ADMONITION_CLOSE,
      "",
    );
  }
  return out.join("\n");
}

type MdNode = {
  type: string;
  value?: string;
  depth?: number;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, string> };
};

export function slugifyRow(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function slugifyHeading(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[^\x00-\x7f]/g, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, "-");
}

function readText(node: MdNode): string {
  if (node.value !== undefined && node.type !== "html") return node.value;
  return (node.children ?? []).map(readText).join("");
}

function isMarker(node: MdNode | undefined, marker: string): boolean {
  return node?.type === "html" && node.value?.trim() === marker;
}

function groupTabs(nodes: MdNode[]): MdNode {
  const tabs: MdNode[] = [];
  for (const node of nodes) {
    if (node.type === "heading" && node.depth === 4) {
      tabs.push({
        type: "docTab",
        data: { hName: "div", hProperties: { dataDocTab: readText(node) } },
        children: [],
      });
    } else {
      tabs.at(-1)?.children?.push(node);
    }
  }
  return {
    type: "docTabs",
    data: { hName: "div", hProperties: { dataDocTabs: "" } },
    children: tabs,
  };
}

function anchorRows(table: MdNode) {
  for (const row of (table.children ?? []).slice(1)) {
    const first = row.children?.[0];
    if (!first) continue;
    row.data = {
      ...row.data,
      hProperties: { id: slugifyRow(readText(first)) },
    };
  }
}

function groupAdmonitions(children: MdNode[]): MdNode[] {
  const out: MdNode[] = [];
  for (let i = 0; i < children.length; i++) {
    const open = ADMONITION_OPEN.exec(
      children[i].type === "html" ? (children[i].value?.trim() ?? "") : "",
    );
    if (!open) {
      out.push(children[i]);
      continue;
    }
    let depth = 0;
    let end = -1;
    for (let j = i + 1; j < children.length; j++) {
      if (
        children[j].type === "html" &&
        ADMONITION_OPEN.test(children[j].value?.trim() ?? "")
      )
        depth++;
      if (isMarker(children[j], ADMONITION_CLOSE)) {
        if (depth === 0) {
          end = j;
          break;
        }
        depth--;
      }
    }
    if (end === -1) {
      out.push(children[i]);
      continue;
    }
    const [, type, kind, title] = open;
    out.push({
      type: "docAdmonition",
      data: {
        hName: "div",
        hProperties: { dataAdmonition: kind, dataType: type, dataTitle: title },
      },
      children: groupAdmonitions(children.slice(i + 1, end)),
    });
    i = end;
  }
  return out;
}

function transform(parent: MdNode) {
  parent.children = groupAdmonitions(parent.children ?? []);
  const out: MdNode[] = [];
  const children = parent.children ?? [];
  for (let i = 0; i < children.length; i++) {
    const node = children[i];
    if (isMarker(node, TABS_OPEN)) {
      const end = children.findIndex(
        (n, j) => j > i && isMarker(n, TABS_CLOSE),
      );
      if (end === -1) {
        out.push(node);
        continue;
      }
      out.push(groupTabs(children.slice(i + 1, end)));
      i = end;
    } else if (
      isMarker(node, ANCHOR_ROWS) &&
      children[i + 1]?.type === "table"
    ) {
      anchorRows(children[i + 1]);
    } else {
      out.push(node);
    }
  }
  parent.children = out;
}

function addHeadingIds(node: MdNode, seen: Map<string, number>) {
  for (const child of node.children ?? []) {
    if (child.type === "heading") {
      const base = slugifyHeading(readText(child));
      const count = seen.get(base) ?? 0;
      seen.set(base, count + 1);
      child.data = {
        ...child.data,
        hProperties: { id: count ? `${base}_${count}` : base },
      };
    } else {
      addHeadingIds(child, seen);
    }
  }
}

export function remarkDocMarkers() {
  return (tree: MdNode) => {
    transform(tree);
    addHeadingIds(tree, new Map());
  };
}

const IMAGE_PREFIX = "../../../docs/user/images/";

export const DOC_IMAGES = import.meta.glob<string>(
  "../../../docs/user/images/*",
  { eager: true, query: "?url", import: "default" },
);

export function resolveDocImage(
  src: string | undefined,
  images: Record<string, string> = DOC_IMAGES,
): string | undefined {
  const match = /^(?:\.\/)?images\/([^/?#]+)$/.exec(src ?? "");
  if (!match) return src;
  return images[`${IMAGE_PREFIX}${match[1]}`] ?? src;
}
