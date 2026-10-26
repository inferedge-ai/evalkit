import { describe, expect, it } from "vitest";
import {
  gitRevision,
  orderWorkspaces,
  othersPersonal,
  sectionRoot,
  splitRef,
  workspaceHint,
} from "./workspace";
import type { WorkspaceInfo } from "./types";

const ws = (name: string, shared = false): WorkspaceInfo => ({
  id: 1,
  name,
  promotes_to: null,
  promoted_from: null,
  type: shared ? "shared" : "personal",
  archived: false,
});

describe("workspace helpers", () => {
  it("splits refs", () => {
    expect(splitRef("finance@ravi")).toEqual({
      name: "finance",
      workspace: "ravi",
    });
    expect(splitRef("finance")).toEqual({ name: "finance", workspace: "" });
  });

  it("reads a git revision", () => {
    expect(gitRevision(undefined)).toBe(null);
    expect(gitRevision({ dirty: true })).toBe(null);
    expect(gitRevision({ sha: "abc", branch: "feat", dirty: true })).toEqual({
      sha: "abc",
      branch: "feat",
      dirty: true,
    });
  });
});

describe("orderWorkspaces", () => {
  it("puts mine first, then shared, then others' personal, each by name", () => {
    const ordered = orderWorkspaces(
      [ws("zed"), ws("prod", true), ws("akshat"), ws("dev", true), ws("ravi")],
      "akshat",
    );
    expect(ordered.map((w) => w.name)).toEqual([
      "akshat",
      "dev",
      "prod",
      "ravi",
      "zed",
    ]);
  });

  it("orders without a personal workspace", () => {
    expect(
      orderWorkspaces([ws("ravi"), ws("dev", true)], null).map((w) => w.name),
    ).toEqual(["dev", "ravi"]);
  });
});

describe("sectionRoot", () => {
  it("maps a detail page to its workspace-scoped list", () => {
    expect(sectionRoot("/datasets/finance@dev/cases")).toBe("/datasets");
    expect(sectionRoot("/experiments/12/case-1")).toBe("/experiments");
    expect(sectionRoot("/runs/support-bot@dev/r1")).toBe("/runs");
    expect(sectionRoot("/agents")).toBe("/agents");
  });

  it("keeps the settings tab and ignores unscoped pages", () => {
    expect(sectionRoot("/workspace/members")).toBe("/workspace/members");
    expect(sectionRoot("/docs/install")).toBe(null);
    expect(sectionRoot("/")).toBe(null);
  });
});

describe("othersPersonal", () => {
  it("is true only for someone else's personal workspace", () => {
    expect(othersPersonal(ws("ravi"), "akshat")).toBe(true);
    expect(othersPersonal(ws("akshat"), "akshat")).toBe(false);
    expect(othersPersonal(ws("dev", true), "akshat")).toBe(false);
  });
});

describe("workspaceHint", () => {
  it("marks someone else's personal workspace read-only", () => {
    expect(workspaceHint(ws("akshat"), "akshat")).toBe("yours");
    expect(workspaceHint(ws("dev", true), "akshat")).toBe("shared");
    expect(workspaceHint(ws("ravi"), "akshat")).toBe("personal · read-only");
    expect(workspaceHint({ ...ws("ravi"), archived: true }, "akshat")).toBe(
      "personal · read-only · archived",
    );
  });
});
