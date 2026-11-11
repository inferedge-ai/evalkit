// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { WorkspaceInfo } from "../lib/types";
import {
  closePage,
  devWorkspace,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { WorkspacePage } from "./WorkspacePage";
import { WorkspacesPage } from "./WorkspacesPage";

vi.mock("../lib/endpoint", () => ({
  ENDPOINT_NOTE: "Reachable from the server.",
  PERSONAL_KEY_NOTE: "Keys live in evalkit.yaml.",
}));

const admin = { ...member({}), is_admin: true };

const personal = (name: string, id: number): WorkspaceInfo => ({
  ...devWorkspace,
  id,
  name,
  type: "personal",
  members: 0,
});

const workspaces: WorkspaceInfo[] = [
  personal("akshat", 1),
  { ...devWorkspace, members: 3, promotes_to: "prod" },
  { ...devWorkspace, id: 3, name: "prod", members: 1, promoted_from: "dev" },
  personal("bob", 4),
  personal("carol", 5),
];

function serveWorkspaces(
  route: (url: string, init?: RequestInit) => Response | undefined = () =>
    undefined,
) {
  stubServer((url, init) => {
    const answer = route(url, init);
    if (answer) return answer;
    if (url.endsWith("/api/workspaces?include_archived=true"))
      return Response.json(workspaces);
  });
}

function openWorkspaces(me = admin) {
  renderPage(
    me,
    "/settings/workspaces",
    <>
      <Route path="/settings/workspaces" element={<WorkspacesPage />} />
      <Route path="/settings/workspace/:tab" element={<WorkspacePage />} />
    </>,
  );
}

const row = (name: string) =>
  screen.queryByText(`@${name}`)?.closest("button") ?? null;

afterEach(() => {
  closePage();
  localStorage.clear();
});

test("an admin sees their own and the shared workspaces, and other people's after Show 2 other personal workspaces", async () => {
  serveWorkspaces();
  openWorkspaces();
  await screen.findByText("@akshat");
  expect(row("akshat")).toHaveTextContent("yours");
  expect(row("dev")).toHaveTextContent("3 members");
  expect(row("dev")).toHaveTextContent("promotes to @prod");
  expect(row("prod")).toHaveTextContent("1 member");
  expect(row("bob")).toBeNull();

  await userEvent.click(
    screen.getByRole("button", { name: "Show 2 other personal workspaces" }),
  );
  expect(row("bob")).toHaveTextContent("personal · read-only");
  expect(row("carol")).toBeInTheDocument();
});

test("the filter finds other people's personal workspaces too", async () => {
  serveWorkspaces();
  openWorkspaces();
  await screen.findByText("@akshat");
  await userEvent.type(screen.getByPlaceholderText("Filter workspaces"), "bo");
  expect(row("bob")).toBeInTheDocument();
  expect(row("dev")).toBeNull();
});

test("clicking a workspace switches to it and opens its settings", async () => {
  serveWorkspaces();
  openWorkspaces();
  await screen.findByText("@prod");
  await userEvent.click(row("prod")!);
  expect(
    await screen.findByRole("heading", { name: "@prod" }),
  ).toBeInTheDocument();
});

test("an admin creates a workspace and lands on its members", async () => {
  const qa = { ...devWorkspace, id: 6, name: "qa", members: 0 };
  let created: unknown = null;
  serveWorkspaces((url, init) => {
    if (url.endsWith("/api/workspaces") && init?.method === "POST") {
      created = JSON.parse(String(init.body));
      return Response.json(qa);
    }
    if (created && url.endsWith("/api/workspaces?include_archived=true"))
      return Response.json([...workspaces, qa]);
  });
  openWorkspaces();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "New workspace" }),
  );
  const dialog = await screen.findByRole("dialog", { name: "New workspace" });
  await user.type(within(dialog).getByLabelText("Name"), " qa ");
  await user.click(
    within(dialog).getByRole("button", { name: "Create workspace" }),
  );
  expect(
    await screen.findByRole("heading", { name: "@qa" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Members" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(created).toEqual({ name: "qa" });
});

test("a name the server refuses shows its error in the dialog", async () => {
  serveWorkspaces((url, init) =>
    url.endsWith("/api/workspaces") && init?.method === "POST"
      ? Response.json(
          { detail: "workspace dev already exists" },
          { status: 409 },
        )
      : undefined,
  );
  openWorkspaces();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "New workspace" }),
  );
  const dialog = await screen.findByRole("dialog", { name: "New workspace" });
  await user.type(within(dialog).getByLabelText("Name"), "dev");
  await user.click(
    within(dialog).getByRole("button", { name: "Create workspace" }),
  );
  expect(
    await within(dialog).findByText("workspace dev already exists"),
  ).toBeInTheDocument();
});

test("a member who opens the page can't create a workspace", async () => {
  serveWorkspaces();
  openWorkspaces(member({ dev: ["writer"] }));
  await screen.findByText("@akshat");
  expect(screen.queryByRole("button", { name: "New workspace" })).toBeNull();
});
