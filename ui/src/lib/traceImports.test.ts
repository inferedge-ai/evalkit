import { describe, expect, it } from "vitest";
import {
  importAction,
  importFraction,
  isActiveImport,
  localToIso,
} from "./traceImports";

const range = {
  since: "2026-10-01T00:00:00Z",
  until: "2026-10-05T00:00:00Z",
};

describe("importFraction", () => {
  it("is how far resume_from got through the range", () => {
    expect(
      importFraction({
        ...range,
        resume_from: "2026-10-02T00:00:00Z",
        status: "running",
      }),
    ).toBe(0.25);
    expect(
      importFraction({ ...range, resume_from: range.since, status: "failed" }),
    ).toBe(0);
  });

  it("is full once done, even when the last trace started early", () => {
    expect(
      importFraction({ ...range, resume_from: range.since, status: "done" }),
    ).toBe(1);
  });
});

describe("importAction", () => {
  it("offers stop while running and continue after failing or stopping", () => {
    expect(importAction("running")).toBe("stop");
    expect(importAction("stopping")).toBeNull();
    expect(importAction("failed")).toBe("continue");
    expect(importAction("stopped")).toBe("continue");
    expect(importAction("done")).toBeNull();
  });
});

describe("isActiveImport", () => {
  it("is true while the server task runs", () => {
    expect(isActiveImport("running")).toBe(true);
    expect(isActiveImport("stopping")).toBe(true);
    expect(isActiveImport("done")).toBe(false);
  });
});

describe("localToIso", () => {
  it("reads a datetime-local value in the browser's zone and sends UTC", () => {
    const iso = localToIso("2026-10-01T09:30");
    expect(iso).toMatch(/Z$/);
    expect(new Date(iso).getTime()).toBe(new Date(2026, 9, 1, 9, 30).getTime());
  });
});
