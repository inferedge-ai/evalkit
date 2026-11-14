// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, useNavigate } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import type { QueueItem } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { ReviewsPage } from "./ReviewsPage";

function queued(caseId: string): QueueItem {
  return {
    dataset: "acme@dev",
    case_id: caseId,
    requested_to: "akshat",
    requested_by: "admin",
    author: "admin",
    requested_at: "2026-11-09T10:00:00Z",
    input: `What about ${caseId}?`,
  };
}

afterEach(closePage);

function openReviews() {
  stubServer((url) => {
    if (!url.includes("/api/review/queue?")) return undefined;
    if (url.includes("state=changes_requested"))
      return Response.json({ items: [queued("acme-gross-margin")] });
    if (url.includes("state=rejected"))
      return Response.json({ items: [queued("acme-rejected-case")] });
    if (url.includes("reviewer=akshat"))
      return Response.json({ items: [queued("acme-revenue")] });
    return Response.json({ items: [] });
  });
  renderPage(
    member({ dev: ["reviewer"] }),
    "/reviews",
    <>
      <Route path="/reviews" element={<ReviewsPage />} />
      <Route path="/datasets/:ref/cases/:caseId" element={<BackButton />} />
    </>,
  );
}

function BackButton() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>Back</button>;
}

test("a reviewer switches from their queue to the cases sent back to authors", async () => {
  openReviews();
  expect(
    await screen.findByRole("link", { name: "acme-revenue" }),
  ).toBeInTheDocument();
  await userEvent
    .setup()
    .click(screen.getByRole("tab", { name: "Changes requested" }));
  expect(
    await screen.findByRole("link", { name: "acme-gross-margin" }),
  ).toBeInTheDocument();
  expect(screen.getByText("1 awaiting an edit")).toBeInTheDocument();
});

test("a reviewer switches to the rejected cases tab", async () => {
  openReviews();
  expect(
    await screen.findByRole("link", { name: "acme-revenue" }),
  ).toBeInTheDocument();
  await userEvent
    .setup()
    .click(screen.getByRole("tab", { name: "Rejected" }));
  expect(
    await screen.findByRole("link", { name: "acme-rejected-case" }),
  ).toBeInTheDocument();
  expect(screen.getByText("1 rejected")).toBeInTheDocument();
});

test("Back from a case returns to the queue the reviewer was on", async () => {
  openReviews();
  const user = userEvent.setup();
  await screen.findByRole("link", { name: "acme-revenue" });
  await user.click(screen.getByRole("tab", { name: "Changes requested" }));
  await user.click(
    await screen.findByRole("link", { name: "acme-gross-margin" }),
  );
  await user.click(await screen.findByRole("button", { name: "Back" }));
  expect(
    await screen.findByRole("link", { name: "acme-gross-margin" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("tab", { name: "Changes requested" }),
  ).toHaveAttribute("aria-selected", "true");
});
