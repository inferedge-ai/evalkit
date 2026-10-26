import { describe, expect, it } from "vitest";
import { errorEntries, messageText, toModel, trajectoryBounds } from "./atif";
import type { AtifTrajectory } from "./types";
import fixture from "./atif_full.fixture.json";

const doc = fixture as unknown as AtifTrajectory;

describe("toModel", () => {
  const model = toModel(doc);

  it("builds one tab per trajectory, main first", () => {
    expect(model.tabs.map((t) => t.label)).toEqual([
      "myagent",
      "researcher",
      "writer",
    ]);
    expect(model.tabs[0].steps).toHaveLength(3);
  });

  it("pairs tool calls with observation results by source_call_id", () => {
    const step = model.tabs[0].steps[1];
    expect(step.toolCalls.map((c) => c.name)).toEqual([
      "web_search",
      "read_file",
    ]);
    expect(step.toolCalls[0].result).toContain("ACME FY24 revenue");
    expect(step.toolCalls[0].isError).toBe(false);
    expect(step.toolCalls[1].isError).toBe(true);
    expect(step.toolCalls[1].result).toContain("ENOENT");
  });

  it("links a tool call to the subagent tab it spawned", () => {
    const [search, read] = model.tabs[0].steps[1].toolCalls;
    expect(search.subagents).toEqual([
      { name: "researcher", tab: "traj-sub-research" },
    ]);
    expect(read.subagents).toEqual([]);
    expect(model.tabs.map((t) => t.id)).toContain("traj-sub-research");
  });

  it("links a subagent by session id, and names one that is not embedded", () => {
    const step = (ref: Record<string, string>) => ({
      ...doc.steps[1],
      observation: {
        results: [
          {
            source_call_id: "call_1",
            content: "ok",
            subagent_trajectory_ref: [ref],
          },
        ],
      },
    });
    const withSession = {
      ...doc,
      steps: [step({ session_id: "sess-r" })],
      subagent_trajectories: [
        { ...doc.subagent_trajectories![0], session_id: "sess-r" },
      ],
    };
    expect(
      toModel(withSession).tabs[0].steps[0].toolCalls[0].subagents,
    ).toEqual([{ name: "researcher", tab: "traj-sub-research" }]);
    const external = { ...doc, steps: [step({ trajectory_path: "sub.json" })] };
    expect(toModel(external).tabs[0].steps[0].toolCalls[0].subagents).toEqual([
      { name: "sub.json", tab: null },
    ]);
  });

  it("carries thinking and per-step tokens", () => {
    const step = model.tabs[0].steps[1];
    expect(step.thinking).toContain("10-K");
    expect(step.tokens).toEqual({ prompt: 1200, completion: 300, cached: 800 });
  });

  it("prefers final_metrics for the totals", () => {
    expect(model.totals.prompt).toBe(3200);
    expect(model.totals.completion).toBe(450);
    expect(model.totals.costUsd).toBe(0.02);
    expect(model.totals.toolCalls).toBe(3);
    expect(model.totals.erroredToolCalls).toBe(1);
  });

  it("sums step metrics when final_metrics is absent", () => {
    const withoutFinal = { ...doc, final_metrics: null };
    const summed = toModel(withoutFinal);
    expect(summed.totals.prompt).toBe(3200 + 500);
    expect(summed.totals.completion).toBe(450 + 80);
  });

  it("renders content-part messages as text", () => {
    expect(messageText(doc.steps[2].message)).toContain("revenue $1.2B");
    expect(messageText("plain")).toBe("plain");
    expect(messageText(null)).toBe("");
  });
});

describe("errorEntries", () => {
  it("lists errored tool calls in trajectory order", () => {
    const model = toModel(doc);
    const entries = errorEntries(model.tabs[0].steps);
    expect(entries).toEqual([
      {
        stepId: model.tabs[0].steps[1].stepId,
        callId: model.tabs[0].steps[1].toolCalls[1].id,
        tool: "read_file",
      },
    ]);
    expect(entries.length).toBe(model.totals.erroredToolCalls);
  });
});

describe("trajectoryBounds", () => {
  it("spans the first to the last timestamped step", () => {
    expect(trajectoryBounds(doc)).toEqual({
      start: "2026-09-01T10:00:00Z",
      end: "2026-09-01T10:01:00Z",
    });
    expect(
      trajectoryBounds({ ...doc, steps: [], subagent_trajectories: [] }),
    ).toEqual({ start: null, end: null });
    expect(
      trajectoryBounds({
        ...doc,
        steps: [doc.steps[0]],
        subagent_trajectories: [],
      }),
    ).toEqual({ start: "2026-09-01T10:00:00Z", end: null });
  });
});
