import { describe, expect, it } from "vitest";
import {
  EXPERIMENT_LIST_LIMIT,
  canResume,
  listCapNote,
  reevaluateStrandsCases,
  resumeHint,
  isActive,
  isIncomplete,
  progressLabel,
  shortProgress,
} from "./experimentStatus";

describe("experiment status", () => {
  it("treats running and evaluating as active", () => {
    expect(isActive("running")).toBe(true);
    expect(isActive("evaluating")).toBe(true);
    expect(isActive("completed")).toBe(false);
    expect(isActive("failed")).toBe(false);
  });
});

describe("resume", () => {
  const exp = { id: 7, dataset: "finance@dev", agent: "mybot@alice" };
  it("follows the server's resumable and pending-runs flags", () => {
    expect(
      canResume({ status: "completed", resumable: true, pending_runs: true }),
    ).toBe(true);
    expect(
      canResume({ status: "completed", resumable: false, pending_runs: false }),
    ).toBe(false);
    expect(
      reevaluateStrandsCases({
        status: "failed",
        resumable: true,
        pending_runs: true,
      }),
    ).toBe(true);
    expect(
      reevaluateStrandsCases({
        status: "failed",
        resumable: true,
        pending_runs: false,
      }),
    ).toBe(false);
    expect(
      reevaluateStrandsCases({
        status: "completed",
        resumable: true,
        pending_runs: true,
      }),
    ).toBe(false);
  });

  it("gives a CLI command a user can paste, or points SDK users at their script", () => {
    expect(resumeHint(exp, "cli")).toBe(
      "evalkit run finance@dev --agent mybot@alice --resume 7",
    );
    expect(resumeHint(exp, "sdk")).toMatch(/^re-run from your script/);
  });
});

describe("list cap", () => {
  it("notes when the list may be cut off", () => {
    expect(listCapNote(12)).toBeNull();
    expect(listCapNote(EXPERIMENT_LIST_LIMIT)).toMatch(/latest 200/);
  });
});

describe("progress", () => {
  it("labels ran and evaluated over total", () => {
    const progress = { total: 40, ran: 12, evaluated: 8 };
    expect(progressLabel(progress)).toBe("12/40 ran · 8/40 evaluated");
    expect(shortProgress(progress)).toBe("8/40");
    expect(isIncomplete(progress)).toBe(true);
    expect(isIncomplete({ total: 5, ran: 5, evaluated: 5 })).toBe(false);
  });
});
