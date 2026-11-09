// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { CaseView, Me, WorkspaceInfo } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { WorkspaceGate } from "../shell/workspace";
import { DatasetCasesTab } from "./DatasetCasesTab";

const me: Me = {
  username: "akshat",
  is_admin: false,
  email: null,
  kind: "human",
  personal_workspace: "akshat",
  roles: { dev: ["writer"] },
};

const workspace: WorkspaceInfo = {
  id: 2,
  name: "dev",
  type: "shared",
  archived: false,
  promotes_to: null,
  promoted_from: null,
};

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

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

test("with a column filter set, select all and Run selected take only the cases the filter shows", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      if (url.endsWith("/api/datasets/support%40dev/cases"))
        return Response.json(CASES);
      if (url.includes("/case-scores")) return Response.json({});
      return Response.json([]);
    }),
  );
  const onRun = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter initialEntries={["/datasets/support@dev/cases"]}>
          <Routes>
            <Route element={<WorkspaceGate />}>
              <Route
                path="/datasets/:ref/cases"
                element={
                  <DatasetCasesTab
                    dataset="support@dev"
                    reviewed={false}
                    onRun={onRun}
                  />
                }
              />
            </Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
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
