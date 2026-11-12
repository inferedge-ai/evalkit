// The authoring markers are documented in CONTRIBUTING.md; docs/site/markers.py
// applies the same rules for the MkDocs site.
const TABS_OPEN = "<!-- tabs -->";
const TABS_CLOSE = "<!-- /tabs -->";
const ANCHOR_ROWS = "<!-- anchor-rows -->";

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

function transform(parent: MdNode) {
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
