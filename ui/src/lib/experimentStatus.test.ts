import { describe, expect, it } from "vitest";
import {
  EXPERIMENT_LIST_LIMIT,
  canResume,
  listCapNote,
  reevaluateStrandsCases,
  resumeHint,
  isActive,
  isIncomplete,
  erroredNote,
  progressLabel,
  shortProgress,
} from "./experimentStatus";
import { scorecard } from "../testFixtures";

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

  it("counts errored and partly errored cases in both labels", () => {
    const progress = {
      total: 6,
      ran: 6,
      evaluated: 6,
      errored: 3,
      partly_errored: 1,
    };
    expect(progressLabel(progress)).toBe(
      "6/6 ran · 6/6 evaluated · 3 errored · 1 partly errored",
    );
    expect(shortProgress(progress)).toBe("6/6 · 3 err · 1 part err");
  });

  it("notes errored and partly errored cases out of the selected cases", () => {
    const base = {
      ...scorecard,
      cases: [],
      selected_cases: 6,
      errored_cases: 0,
      partly_errored_cases: 0,
    };
    expect(erroredNote(base)).toBeNull();
    expect(erroredNote({ ...base, errored_cases: 3 })).toBe(
      "3 of 6 cases errored: no run completed with an answer, so they are left out of every score.",
    );
    expect(erroredNote({ ...base, partly_errored_cases: 1 })).toBe(
      "1 of 6 cases partly errored: scored only on the runs that completed.",
    );
  });
});
