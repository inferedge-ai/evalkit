import { describe, expect, it } from "vitest";
import {
  promotedImpliesApproved,
  promotionSide,
  twinCell,
  twinLine,
} from "./promotion";
import type { TwinStatus, WorkspaceInfo } from "./types";

const ws = (extra: Partial<WorkspaceInfo>): WorkspaceInfo => ({
  id: 1,
  name: "dev",
  type: "shared",
  archived: false,
  promotes_to: null,
  promoted_from: null,
  ...extra,
});

const twin = (extra: Partial<TwinStatus>): TwinStatus => ({
  workspace: "prod",
  state: "same",
  content_hash: "abc123",
  review: null,
  overlays_differ: false,
  href: "/datasets/finance@prod/cases/x",
  ...extra,
});

describe("promotionSide", () => {
  it("reads the link direction off the workspace", () => {
    expect(promotionSide(undefined)).toBeNull();
    expect(promotionSide(ws({}))).toBeNull();
    expect(promotionSide(ws({ promotes_to: "prod" }))).toBe("source");
    expect(promotionSide(ws({ promoted_from: "dev" }))).toBe("target");
  });
});

describe("twinLine", () => {
  it("names the other workspace and its state", () => {
    expect(twinLine(twin({ state: "absent" }), "source")).toBe("Not in prod");
    expect(
      twinLine(twin({ state: "absent", workspace: "dev" }), "target"),
    ).toBe("Removed in dev");
    expect(twinLine(twin({}), "source")).toBe("Promoted to prod");
    expect(twinLine(twin({ workspace: "dev" }), "target")).toBe(
      "Same version in dev",
    );
    expect(twinLine(twin({ state: "differs" }), "source")).toBe(
      "prod has abc123",
    );
    expect(
      twinLine(
        twin({
          review: { state: "approved", requested_to: null, updated_at: null },
          overlays_differ: true,
        }),
        "source",
      ),
    ).toBe("Promoted to prod · approved · overlays differ from prod");
  });
});

describe("twinCell", () => {
  it("gives the linked-workspace column one chip per state", () => {
    expect(twinCell(null, "source")).toBeNull();
    expect(twinCell(twin({}), null)).toBeNull();
    expect(twinCell(twin({}), "source")).toEqual({
      label: "Promoted",
      cls: "chip-pass",
    });
    expect(twinCell(twin({ state: "differs" }), "source")?.label).toBe(
      "ahead of prod",
    );
    expect(twinCell(twin({ state: "absent" }), "source")?.label).toBe(
      "not in prod",
    );
    expect(twinCell(twin({ workspace: "dev" }), "target")?.label).toBe("same");
    expect(
      twinCell(twin({ state: "differs", workspace: "dev" }), "target")?.label,
    ).toBe("newer in dev");
    expect(
      twinCell(twin({ state: "absent", workspace: "dev" }), "target")?.label,
    ).toBe("removed in dev");
  });
});

describe("promotedImpliesApproved", () => {
  it("hides the Approved chip only when the source row says Promoted", () => {
    expect(promotedImpliesApproved(twin({}), "source", "approved")).toBe(true);
    expect(promotedImpliesApproved(twin({}), "target", "approved")).toBe(false);
    expect(
      promotedImpliesApproved(twin({ state: "differs" }), "source", "approved"),
    ).toBe(false);
    expect(promotedImpliesApproved(twin({}), "source", "draft")).toBe(false);
  });
});
