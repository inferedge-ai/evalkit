// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import type { CaseView, Dataset } from "../lib/types";
import {
  closePage,
  failure,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { DatasetPage } from "./DatasetPage";

const support: Dataset = {
  id: 5,
  workspace: "dev",
  workspace_is_shared: true,
  name: "support",
  kind: "suite",
  last_sync: null,
  ref: "support@dev",
};

const refundPolicy: CaseView = {
  case_ref: 1,
  case_id: "refund-policy",
  content_hash: "abc",
  input: { question: "How long do refunds take?" },
  expected: null,
  evaluators: {},
  effective_evaluators: {},
  tags: [],
  split_tags: [],
  source: null,
  files: [],
  review_state: null,
};

afterEach(closePage);

async function pickCases(cases: () => Response) {
  stubServer((url) => {
    if (url.endsWith("/api/datasets/support%40dev"))
      return Response.json(support);
    if (url.endsWith("/api/datasets/support%40dev/cases")) return cases();
    if (url.endsWith("/api/judge-default"))
      return Response.json({ detail: "no default judge" }, { status: 404 });
  });
  renderPage(
    member({ dev: ["writer"] }),
    "/datasets/support@dev/experiments",
    <Route path="/datasets/:ref/:tab" element={<DatasetPage />} />,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Run experiment" }),
  );
  await user.click(await screen.findByRole("button", { name: "Pick cases" }));
  return user;
}

test("filtering the case picker to nothing says no case matches", async () => {
  const user = await pickCases(() => Response.json([refundPolicy]));
  await screen.findByText("How long do refunds take?");
  await user.type(
    screen.getByPlaceholderText("Filter by id or question"),
    "invoices",
  );
  expect(
    await screen.findByText("No cases match this filter"),
  ).toBeInTheDocument();
});

test("a dataset with no cases says so in the case picker", async () => {
  await pickCases(() => Response.json([]));
  expect(
    await screen.findByText("This dataset has no cases"),
  ).toBeInTheDocument();
  expect(screen.queryByText("No cases match this filter")).toBeNull();
});

test("when the cases cannot load, the case picker stops showing placeholders", async () => {
  await pickCases(() => failure("store unavailable"));
  const dialog = await screen.findByRole("dialog");
  expect(
    await within(dialog).findByText(/store unavailable/),
  ).toBeInTheDocument();
  expect(dialog.querySelector('[data-slot="skeleton"]')).toBeNull();
});
