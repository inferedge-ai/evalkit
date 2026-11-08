// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { ColumnsMenu, DataGrid, useDataGrid } from "./DataGrid";
import type { GridColumn } from "./DataGrid";

interface Run {
  id: string;
  agent: string;
  status: string;
  score: number;
}

const RUNS: Run[] = [
  { id: "r1", agent: "support-bot", status: "completed", score: 0.4 },
  { id: "r2", agent: "billing-bot", status: "failed", score: 0.9 },
];

const AGENT: GridColumn<Run> = {
  colId: "agent",
  field: "agent",
  headerName: "agent",
};
const STATUS: GridColumn<Run> = {
  colId: "status",
  field: "status",
  headerName: "status",
};
const SCORE: GridColumn<Run> = {
  colId: "score",
  field: "score",
  headerName: "score",
  initialHide: true,
};
const ALL = [AGENT, STATUS, SCORE];

const rowId = (run: Run) => run.id;

function RunsTable({
  columns = ALL,
  error = false,
}: {
  columns?: GridColumn<Run>[];
  error?: boolean;
}) {
  const grid = useDataGrid<Run>("runs");
  return (
    <>
      <ColumnsMenu grid={grid} />
      {error ? (
        <div>the harness is down</div>
      ) : (
        <DataGrid grid={grid} rows={RUNS} columns={columns} getRowId={rowId} />
      )}
    </>
  );
}

function shownColumns(): string[] {
  return [...document.querySelectorAll(".ag-header-cell")]
    .sort(
      (a, b) =>
        Number(a.getAttribute("aria-colindex")) -
        Number(b.getAttribute("aria-colindex")),
    )
    .map((h) => h.getAttribute("col-id") ?? "");
}

function sortOf(colId: string): string | null | undefined {
  return document
    .querySelector(`.ag-header-cell[col-id="${colId}"]`)
    ?.getAttribute("aria-sort");
}

function clickHeader(colId: string) {
  fireEvent.click(
    document.querySelector(
      `.ag-header-cell[col-id="${colId}"] .ag-header-cell-label`,
    )!,
  );
}

async function openColumnsMenu() {
  fireEvent.click(await screen.findByRole("button", { name: "Columns" }));
}

function menuOrder(): string[] {
  return screen
    .getAllByRole("checkbox")
    .map((box) => box.closest("label")?.textContent ?? "");
}

// The grid hands its events over on a timer; the menu shows a change only once
// its handler, which also saves the layout, has run.
async function toggleColumn(colId: string) {
  const box = await screen.findByRole("checkbox", { name: colId });
  const was = (box as HTMLInputElement).checked;
  fireEvent.click(box);
  await waitFor(() =>
    expect(screen.getByRole("checkbox", { name: colId })).toHaveProperty(
      "checked",
      !was,
    ),
  );
}

async function moveUp(colId: string, after: string[]) {
  fireEvent.click(screen.getByRole("button", { name: `move ${colId} up` }));
  await waitFor(() => expect(menuOrder()).toEqual(after));
}

function saveEarlierLayout(state: object[]) {
  localStorage.setItem("evalkit.table.runs", JSON.stringify(state));
}

afterEach(() => localStorage.clear());

test("a table comes back with the order, sort and hidden columns it was left with", async () => {
  const first = render(<RunsTable />);
  await waitFor(() => expect(shownColumns()).toEqual(["agent", "status"]));
  clickHeader("agent");
  await openColumnsMenu();
  await toggleColumn("score");
  await moveUp("score", ["agent", "score", "status"]);
  await moveUp("score", ["score", "agent", "status"]);
  await toggleColumn("status");
  first.unmount();

  render(<RunsTable />);
  await waitFor(() => expect(shownColumns()).toEqual(["score", "agent"]));
  expect(sortOf("agent")).toBe("ascending");
  await openColumnsMenu();
  expect(screen.getByRole("checkbox", { name: "status" })).not.toBeChecked();
});

test("columns that arrive with the data take the place and visibility saved for them", async () => {
  saveEarlierLayout([
    { colId: "score", hide: false },
    { colId: "agent", sort: "desc" },
    { colId: "status", hide: true },
  ]);

  const view = render(<RunsTable columns={[AGENT]} />);
  await waitFor(() => expect(sortOf("agent")).toBe("descending"));
  view.rerender(<RunsTable columns={ALL} />);

  await waitFor(() => expect(shownColumns()).toEqual(["score", "agent"]));
  expect(sortOf("agent")).toBe("descending");
});

test("a change made while some columns are absent keeps their saved layout", async () => {
  saveEarlierLayout([
    { colId: "status", hide: true },
    { colId: "agent" },
    { colId: "score", hide: false },
  ]);

  const first = render(<RunsTable columns={[AGENT, SCORE]} />);
  await waitFor(() => expect(shownColumns()).toEqual(["agent", "score"]));
  await openColumnsMenu();
  await moveUp("score", ["score", "agent"]);
  first.unmount();

  render(<RunsTable />);
  await waitFor(() => expect(shownColumns()).toEqual(["score", "agent"]));
  await openColumnsMenu();
  expect(menuOrder()).toEqual(["status", "score", "agent"]);
});

test("a column added since the layout was saved sits after its default neighbour", async () => {
  saveEarlierLayout([{ colId: "agent" }, { colId: "score", hide: false }]);

  render(<RunsTable />);

  await waitFor(() =>
    expect(shownColumns()).toEqual(["agent", "status", "score"]),
  );
});

test("a table that comes back after an error on the same page keeps its layout", async () => {
  saveEarlierLayout([{ colId: "status", hide: true }]);
  const view = render(<RunsTable />);
  await waitFor(() => expect(shownColumns()).toEqual(["agent"]));

  view.rerender(<RunsTable error />);
  await screen.findByText("the harness is down");
  view.rerender(<RunsTable />);

  await waitFor(() => expect(shownColumns()).toEqual(["agent"]));
});

describe("a missing or unreadable saved layout shows the default columns", () => {
  test.each([
    ["nothing saved", null],
    ["not JSON", "{oops"],
    [
      "the old table layout",
      '{"sorting":[{"id":"agent","desc":true}],"order":["status","agent"],"hidden":{"score":true},"sizing":{}}',
    ],
    ["entries without a column id", '[{"hide":true},null]'],
  ])("%s", async (_, raw) => {
    if (raw !== null) localStorage.setItem("evalkit.table.runs", raw);

    render(<RunsTable />);

    await waitFor(() => expect(shownColumns()).toEqual(["agent", "status"]));
    expect(sortOf("agent")).toBe("none");
  });
});

test("Reset brings back the default columns, also after a reload", async () => {
  saveEarlierLayout([
    { colId: "status", sort: "asc" },
    { colId: "score", hide: false },
    { colId: "agent", hide: true },
  ]);
  const first = render(<RunsTable />);
  await waitFor(() => expect(shownColumns()).toEqual(["status", "score"]));

  await openColumnsMenu();
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  await waitFor(() =>
    expect(screen.getByRole("checkbox", { name: "score" })).not.toBeChecked(),
  );
  expect(shownColumns()).toEqual(["agent", "status"]);
  expect(sortOf("status")).toBe("none");
  first.unmount();

  render(<RunsTable />);
  await waitFor(() => expect(shownColumns()).toEqual(["agent", "status"]));
  expect(sortOf("status")).toBe("none");
});
