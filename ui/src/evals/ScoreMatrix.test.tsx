// @vitest-environment jsdom
import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { ColumnGroup } from "../lib/scoreSummary";
import type { CaseRow, Report } from "../lib/types";
import { ScoreMatrix, type MatrixRow } from "./ScoreMatrix";

function caseRow(case_id: string, accuracy: number, coverage: number): CaseRow {
  return {
    case_id,
    scores: { accuracy, coverage },
    explanations: {},
    gauges: {},
    errors: {},
    run_errors: [],
    repetitions: 1,
    overall: (accuracy + coverage) / 2,
    baseline_overall: null,
    delta: null,
  };
}

const CASES = [
  caseRow("refund-policy", 0.9, 0.2),
  caseRow("late-fee", 0.1, 0.8),
  caseRow("card-limit", 0.5, 0.5),
];

const REPORT: Report = {
  experiment_id: "exp-1",
  name: "exp-1",
  dataset: "support@dev",
  metadata: {},
  cases: CASES,
  evaluators: [
    {
      name: "accuracy",
      score: 0.5,
      cases: 3,
      errors: 0,
      bucket: "content",
      source: "builtin",
    },
    {
      name: "coverage",
      score: 0.5,
      cases: 3,
      errors: 0,
      bucket: "content",
      source: "builtin",
    },
  ],
  buckets: { content: 0.5 },
  gauges: [],
  evaluator_columns: ["accuracy", "coverage"],
  baseline_id: null,
};

const GROUPS: ColumnGroup[] = [
  { label: "content", kind: "score", cols: ["accuracy", "coverage"] },
];

function Matrix({ sortBy, rows }: { sortBy: string; rows: CaseRow[] }) {
  return (
    <ScoreMatrix
      id="experiment-cases"
      report={REPORT}
      groups={GROUPS}
      rows={rows.map((row): MatrixRow => ({ case_id: row.case_id, row }))}
      filtered={rows.length !== CASES.length}
      caseCell={(r) => r.case_id}
      sortBy={sortBy}
    />
  );
}

function caseOrder(): string[] {
  return [
    ...document.querySelectorAll(".ag-pinned-left-cols-container .ag-row"),
  ]
    .sort(
      (a, b) =>
        Number(a.getAttribute("row-index")) -
        Number(b.getAttribute("row-index")),
    )
    .map((row) => row.textContent ?? "");
}

afterEach(() => localStorage.clear());

test("focusing an evaluator sorts the cases by it, worst first, while the rows change", async () => {
  const view = render(<Matrix sortBy="" rows={CASES} />);
  await waitFor(() => expect(caseOrder()).toHaveLength(3));
  fireEvent.click(
    document.querySelector(
      '.ag-header-cell[col-id="coverage"] .ag-header-cell-label',
    )!,
  );
  await waitFor(() =>
    expect(caseOrder()).toEqual(["refund-policy", "card-limit", "late-fee"]),
  );
  await new Promise((resolve) => setTimeout(resolve, 20));

  view.rerender(<Matrix sortBy="accuracy" rows={CASES.slice(0, 2)} />);

  await waitFor(() =>
    expect(caseOrder()).toEqual(["late-fee", "refund-policy"]),
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(caseOrder()).toEqual(["late-fee", "refund-policy"]);
});
