import { describe, expect, it } from "vitest";
import { levelOne, rankItems, splitItems, workspaceItems } from "./palette";
import type { PaletteItem } from "./palette";
import type { CaseView, WorkspaceInfo } from "./types";

const item = (key: string, ...terms: string[]): PaletteItem => ({
  key,
  label: key,
  tag: "case",
  route: `/x/${key}`,
  terms: [key, ...terms],
});

const items = [
  item("tcs-revenue", "What was TCS revenue?"),
  item("infy-margin", "Infosys margin"),
  item("revenue-growth"),
];

describe("rankItems", () => {
  it("puts exact, then prefix, then substring matches first", () => {
    expect(rankItems(items, "revenue").map((i) => i.key)).toEqual([
      "revenue-growth",
      "tcs-revenue",
    ]);
    expect(rankItems(items, "tcs-revenue")[0].key).toBe("tcs-revenue");
  });

  it("matches secondary terms case-insensitively", () => {
    expect(rankItems(items, "INFOSYS").map((i) => i.key)).toEqual([
      "infy-margin",
    ]);
  });

  it("returns items in order for a blank query, up to the limit", () => {
    expect(rankItems(items, " ", 2).map((i) => i.key)).toEqual([
      "tcs-revenue",
      "infy-margin",
    ]);
  });
});

describe("levelOne", () => {
  const pages = [
    { to: "/reviews", label: "Reviews" },
    { to: "/runs", label: "Runs" },
  ];

  it("lists every search kind and page with no query", () => {
    const entries = levelOne("", pages, items);
    expect(entries.filter((e) => e.type === "kind")).toHaveLength(6);
    expect(entries.filter((e) => e.type === "page")).toHaveLength(2);
    expect(entries.some((e) => e.type === "item")).toBe(false);
  });

  it("narrows kinds by prefix and pages by substring", () => {
    const entries = levelOne("re", pages, items);
    expect(
      entries
        .filter((e) => e.type === "page")
        .map((e) => e.type === "page" && e.label),
    ).toEqual(["Reviews"]);
    expect(entries.filter((e) => e.type === "kind")).toHaveLength(0);
  });

  it("puts the workspace switch first for w", () => {
    expect(levelOne("w", pages, items)[0]).toMatchObject({
      type: "kind",
      kind: "workspace",
    });
  });

  it("falls through to direct item hits for two or more characters", () => {
    const entries = levelOne("infy", pages, items);
    expect(entries).toEqual([{ type: "item", item: items[1] }]);
  });
});

describe("splitItems", () => {
  const ex = (case_id: string, tags?: string[]) =>
    ({ case_id, split_tags: tags ?? [] }) as unknown as CaseView;

  it("makes one item per tag with its case count and a tag-filtered route", () => {
    const got = splitItems("fin ance", [
      ex("a", ["smoke", "q&a"]),
      ex("b", ["smoke"]),
      ex("c"),
    ]);
    expect(got).toEqual([
      {
        key: "split:fin ance:smoke",
        label: "smoke",
        sub: "fin ance · 2 cases",
        tag: "split",
        route: "/datasets/fin%20ance/cases?tag=smoke",
        terms: ["smoke"],
      },
      {
        key: "split:fin ance:q&a",
        label: "q&a",
        sub: "fin ance · 1 cases",
        tag: "split",
        route: "/datasets/fin%20ance/cases?tag=q%26a",
        terms: ["q&a"],
      },
    ]);
  });
});

describe("workspaceItems", () => {
  const ws = (name: string, shared = false): WorkspaceInfo => ({
    id: 1,
    name,
    promotes_to: null,
    promoted_from: null,
    type: shared ? "shared" : "personal",
    archived: false,
  });

  it("makes one switch item per workspace and marks the current one", () => {
    const got = workspaceItems(
      [ws("akshat"), ws("dev", true)],
      "dev",
      "akshat",
    );
    expect(got.map((i) => [i.label, i.sub, i.switchTo])).toEqual([
      ["@akshat", "yours", "akshat"],
      ["@dev", "shared · current", "dev"],
    ]);
    expect(rankItems(got, "de").map((i) => i.switchTo)).toEqual(["dev"]);
  });
});
