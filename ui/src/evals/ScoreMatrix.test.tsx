// @vitest-environment jsdom
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { describeTerm } from "../lib/glossary";
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

function Matrix({
  sortBy,
  rows = CASES,
  defaultHidden,
  caseCell = (r) => r.case_id,
}: {
  sortBy: string;
  rows?: CaseRow[];
  defaultHidden?: string[];
  caseCell?: (r: MatrixRow) => string;
}) {
  return (
    <ScoreMatrix
      id="experiment-cases"
      report={REPORT}
      groups={GROUPS}
      rows={rows.map((row): MatrixRow => ({ case_id: row.case_id, row }))}
      filtered={rows.length !== CASES.length}
      caseCell={caseCell}
      defaultHidden={defaultHidden}
      sortBy={sortBy}
    />
  );
}

function sortOf(colId: string): string | null | undefined {
  return document
    .querySelector(`.ag-header-cell[col-id="${colId}"]`)
    ?.getAttribute("aria-sort");
}

// AG Grid hands its events over on timers; run them all before asserting that
// something did not happen.
async function settle() {
  await act(() => vi.runAllTimersAsync());
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

afterEach(() => {
  localStorage.clear();
  vi.useRealTimers();
});

test("focusing an evaluator sorts the cases by it, worst first, while the rows change", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const view = render(<Matrix sortBy="" />);
  await waitFor(() => expect(caseOrder()).toHaveLength(3));
  fireEvent.click(
    document.querySelector(
      '.ag-header-cell[col-id="coverage"] .ag-header-cell-label',
    )!,
  );
  await waitFor(() =>
    expect(caseOrder()).toEqual(["refund-policy", "card-limit", "late-fee"]),
  );
  await settle();

  view.rerender(<Matrix sortBy="accuracy" rows={CASES.slice(0, 2)} />);

  await waitFor(() =>
    expect(caseOrder()).toEqual(["late-fee", "refund-policy"]),
  );
  await settle();
  expect(caseOrder()).toEqual(["late-fee", "refund-policy"]);
});

test("focusing a hidden evaluator shows its column, and clearing the focus drops its sort", async () => {
  const view = render(<Matrix sortBy="" defaultHidden={["coverage"]} />);
  await waitFor(() => expect(caseOrder()).toHaveLength(3));
  expect(sortOf("coverage")).toBeUndefined();

  view.rerender(<Matrix sortBy="coverage" defaultHidden={["coverage"]} />);
  await waitFor(() => expect(sortOf("coverage")).toBe("ascending"));

  view.rerender(<Matrix sortBy="" defaultHidden={["coverage"]} />);
  await waitFor(() => expect(sortOf("coverage")).toBe("none"));
  expect(caseOrder()).toEqual(["refund-policy", "late-fee", "card-limit"]);
});

test("each refresh shows the rows' new data even where the sorted value stays the same", async () => {
  const withRunErrors = (r: MatrixRow) =>
    r.row?.run_errors.length ? `${r.case_id} · run error` : r.case_id;
  const judged = (error: string): CaseRow => ({
    ...caseRow("late-fee", 0.1, 0.8),
    scores: { coverage: 0.8 },
    errors: { accuracy: error },
  });
  const view = render(
    <Matrix
      sortBy=""
      rows={[CASES[0], judged("judge timed out")]}
      caseCell={withRunErrors}
    />,
  );
  await waitFor(() => expect(caseOrder()).toHaveLength(2));

  view.rerender(
    <Matrix
      sortBy=""
      rows={[
        { ...CASES[0], run_errors: ["harness unreachable"] },
        judged("judge rate limited"),
      ]}
      caseCell={withRunErrors}
    />,
  );

  await waitFor(() =>
    expect(caseOrder()).toEqual(["refund-policy · run error", "late-fee"]),
  );
  expect(
    document
      .querySelector('.ag-row[row-id="late-fee"] [col-id="accuracy"] span')
      ?.getAttribute("title"),
  ).toBe("judge rate limited");
});

const COVERAGE = "answer_rubric_coverage";
const COVERAGE_TEXT = describeTerm(COVERAGE)!;

function GlossaryMatrix() {
  return (
    <ScoreMatrix
      id="experiment-cases"
      report={REPORT}
      groups={[{ label: "content", kind: "score", cols: [COVERAGE] }]}
      rows={CASES.map((row): MatrixRow => ({
        case_id: row.case_id,
        row: { ...row, scores: { [COVERAGE]: row.scores.coverage } },
      }))}
      filtered={false}
      caseCell={(r) => r.case_id}
    />
  );
}

async function findHeader(colId: string): Promise<HTMLElement> {
  return waitFor(() => {
    const header = document.querySelector<HTMLElement>(
      `.ag-header-cell[col-id="${colId}"]`,
    );
    expect(header).not.toBeNull();
    return header!;
  });
}

test("an evaluator's header ⓘ shows its description without sorting, and its label still sorts", async () => {
  const user = userEvent.setup();
  render(<GlossaryMatrix />);
  const header = await findHeader(COVERAGE);
  const tip = await within(header).findByLabelText(COVERAGE_TEXT);

  await user.hover(tip);
  expect(await screen.findByText(COVERAGE_TEXT)).toBeVisible();
  await user.click(tip);
  expect(sortOf(COVERAGE)).toBe("none");

  await user.click(within(header).getByText("coverage"));
  await waitFor(() => expect(sortOf(COVERAGE)).toBe("ascending"));
  expect(caseOrder()).toEqual(["refund-policy", "card-limit", "late-fee"]);
});

test("a keyboard user tabbing across the header stops on an evaluator's ⓘ and reads its description", async () => {
  const user = userEvent.setup();
  render(<GlossaryMatrix />);
  const header = await findHeader(COVERAGE);
  const tip = await within(header).findByLabelText(COVERAGE_TEXT);

  for (let i = 0; i < 10 && document.activeElement !== header; i++)
    await user.tab();
  expect(header).toHaveFocus();
  expect(screen.queryByText(COVERAGE_TEXT)).not.toBeInTheDocument();

  await user.tab();
  expect(tip).toHaveFocus();
  expect(await screen.findByText(COVERAGE_TEXT)).toBeVisible();
});
