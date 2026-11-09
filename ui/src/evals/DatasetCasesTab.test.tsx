// @vitest-environment jsdom
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { CaseView } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { DatasetCasesTab } from "./DatasetCasesTab";

function supportCase(caseId: string, tags: string[]): CaseView {
  return {
    case_ref: 1,
    case_id: caseId,
    content_hash: "abc",
    input: { question: `What about ${caseId}?` },
    expected: null,
    evaluators: {},
    effective_evaluators: {},
    tags,
    split_tags: tags,
    source: null,
    files: [],
    review_state: null,
  };
}

const CASES = [
  supportCase("refund-window", ["billing"]),
  supportCase("refund-partial", ["billing"]),
  supportCase("reset-password", ["account"]),
];

afterEach(closePage);

test("with a column filter set, select all and Run selected take only the cases the filter shows", async () => {
  stubServer((url) => {
    if (url.endsWith("/api/datasets/support%40dev/cases"))
      return Response.json(CASES);
    if (url.includes("/case-scores")) return Response.json({});
  });
  const onRun = vi.fn();
  renderPage(
    member({ dev: ["writer"] }),
    "/datasets/support@dev/cases",
    <Route
      path="/datasets/:ref/cases"
      element={
        <DatasetCasesTab dataset="support@dev" reviewed={false} onRun={onRun} />
      }
    />,
  );
  await screen.findByText("3 cases");

  fireEvent.click(
    document.querySelector(
      '.ag-header-cell[col-id="tags"] .ag-header-cell-filter-button',
    )!,
  );
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Filter Value" }),
    "billing",
  );
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  await screen.findByText("2 cases");

  fireEvent.click(
    screen.getByRole("checkbox", { name: "Column with Header Selection" }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Run selected" }));

  expect(onRun).toHaveBeenCalledTimes(1);
  expect(onRun.mock.calls[0][0].toSorted()).toEqual([
    "refund-partial",
    "refund-window",
  ]);
});
