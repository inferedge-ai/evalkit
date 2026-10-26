import { describe, expect, it } from "vitest";
import { experimentCaseLink, runTitle } from "./runs";
import type { RunListItem } from "./types";

const mk = (run_id: string, title: string | null): RunListItem => ({
  run_id,
  title,
  user_id: null,
  client_id: null,
  created_at: null,
});

const runs = [mk("a", "First run"), mk("b", null)];

describe("runTitle", () => {
  it("returns the matching run's title", () => {
    expect(runTitle(runs, "a")).toBe("First run");
  });

  it("returns null when the matched run has no title", () => {
    expect(runTitle(runs, "b")).toBeNull();
  });

  it("returns null when the run is not in the listing", () => {
    expect(runTitle(runs, "z")).toBeNull();
  });
});

describe("experimentCaseLink", () => {
  it("links a session recorded by an experiment to its case", () => {
    const linked = {
      ...mk("s", null),
      experiment_id: 12,
      case_id: "case 1",
    };
    expect(experimentCaseLink([linked], "s")).toBe("/experiments/12/case%201");
  });

  it("returns null for a session no experiment recorded", () => {
    expect(experimentCaseLink(runs, "a")).toBeNull();
    expect(experimentCaseLink(runs, "z")).toBeNull();
  });
});

import { trajectoryAvailable, trajectoryBadge } from "./runs";

describe("trajectoryAvailable", () => {
  it("accepts valid and partial", () => {
    expect(trajectoryAvailable("valid")).toBe(true);
    expect(trajectoryAvailable("partial")).toBe(true);
    expect(trajectoryAvailable("unavailable")).toBe(false);
    expect(trajectoryAvailable(null)).toBe(false);
  });
});

describe("trajectoryBadge", () => {
  it("names the source, mapping and partial state", () => {
    expect(trajectoryBadge({ trajectory_source: "otel" }, "valid", 3)).toBe(
      "traces · mapping v3",
    );
    expect(
      trajectoryBadge({ trajectory_source: "otel" }, "partial", null),
    ).toBe("traces · no mapping · partial");
    expect(trajectoryBadge({}, "valid", null)).toBeNull();
    expect(trajectoryBadge({}, "partial", null)).toBe("partial");
  });
});
