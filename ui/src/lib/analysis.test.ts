import { describe, expect, it } from "vitest";
import { costAnalysis, timingAnalysis } from "./analysis";
import { toModel } from "./atif";
import type { AtifTrajectory } from "./types";
import fixture from "./atif_full.fixture.json";

const doc = fixture as unknown as AtifTrajectory;
const model = toModel(doc);

describe("costAnalysis", () => {
  const analysis = costAnalysis(model);

  it("summarizes spend from the trajectory totals", () => {
    expect(analysis.summary.cost).toBe(0.02);
    expect(analysis.summary.calls).toBe(3);
    expect(analysis.summary.steps).toBe(7);
    expect(analysis.summary.input).toBe(3200);
    expect(analysis.summary.output).toBe(450);
    expect(analysis.summary.cache_read).toBe(2300);
  });

  it("rolls up llm calls per model, subagents on their own model", () => {
    const main = analysis.by_model.find((r) => r.key === "gpt-5.2")!;
    expect(main.calls).toBe(2);
    expect(main.input).toBe(3200);
    expect(main.output).toBe(450);
    expect(main.cache_read).toBe(2300);
    expect(main.cost).toBeCloseTo(0.02);
    expect(main.unpriced).toBe(false);

    const sub = analysis.by_model.find((r) => r.key === "gpt-5.2-mini")!;
    expect(sub.calls).toBe(1);
    expect(sub.input).toBe(500);
    expect(sub.unpriced).toBe(true);
    expect(analysis.summary.unpriced_models).toEqual(["gpt-5.2-mini"]);
  });

  it("rolls up per agent, skipping agents with no llm calls", () => {
    expect(analysis.by_agent.map((r) => r.key)).toEqual([
      "myagent",
      "researcher",
    ]);
    expect(analysis.by_agent[0].cost).toBeCloseTo(0.02);
  });

  it("counts tool calls and errors across every trajectory", () => {
    const byName = Object.fromEntries(
      analysis.tool_calls.map((r) => [r.key, r]),
    );
    expect(byName.web_search).toMatchObject({ calls: 1, errors: 0 });
    expect(byName.read_file).toMatchObject({ calls: 1, errors: 1 });
    expect(byName.fetch_url).toMatchObject({ calls: 1, errors: 0 });
  });
});

describe("timingAnalysis", () => {
  const timing = timingAnalysis(model);

  it("derives step durations from consecutive timestamps", () => {
    expect(timing.rows.map((r) => [r.step, r.start_ms, r.duration_ms])).toEqual(
      [
        [1, 0, 5_000],
        [2, 5_000, 55_000],
        [3, 60_000, null],
      ],
    );
  });

  it("computes wall clock and counts", () => {
    expect(timing.wall_ms).toBe(60_000);
    expect(timing.steps).toBe(7);
    expect(timing.timed_steps).toBe(2);
  });

  it("ranks the slowest steps", () => {
    expect(timing.slowest.map((r) => r.step)).toEqual([2, 1]);
    expect(timing.slowest[0].tools).toEqual(["web_search", "read_file"]);
  });

  it("handles trajectories without timestamps", () => {
    const bare = timingAnalysis(
      toModel({
        schema_version: "ATIF-v1.8",
        steps: [{ step_id: 1, source: "user" as const }],
      }),
    );
    expect(bare.wall_ms).toBe(null);
    expect(bare.timed_steps).toBe(0);
    expect(bare.rows).toEqual([]);
  });
});
