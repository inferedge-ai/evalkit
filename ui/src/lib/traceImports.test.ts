import { describe, expect, it } from "vitest";
import { importAction, isActiveImport, localToIso } from "./traceImports";

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
