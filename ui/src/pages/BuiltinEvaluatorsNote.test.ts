import { describe, expect, it } from "vitest";
import { splitBuiltins } from "./BuiltinEvaluatorsNote";

describe("built-in evaluators note", () => {
  it("splits server-capable built-ins from local-only ones", () => {
    expect(
      splitBuiltins([
        { name: "answer_rubrics", uses_judge: true, local_only: false },
        { name: "max_response_time", uses_judge: false, local_only: true },
        { name: "presentation_judge", uses_judge: true, local_only: false },
      ]),
    ).toEqual({
      server: ["answer_rubrics", "presentation_judge"],
      local: ["max_response_time"],
    });
  });
});
