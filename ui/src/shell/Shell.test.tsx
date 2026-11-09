// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { PageHeader } from "../components/widgets/PageHeader";
import { queryClient } from "../lib/query";
import type { Me, WorkspaceInfo } from "../lib/types";
import { MeContext } from "./capabilities";
import { ShellOrBare, WorkspaceOrPublic } from "./layout";
import { ThemeProvider } from "./theme";

vi.mock("../extensions", () => ({
  extensions: {
    routes: [],
    nav: [{ to: "/billing", label: "Billing" }],
    moreNav: [{ to: "/usage", label: "Usage" }],
    adminNav: [{ to: "/tenants", label: "Tenants" }],
  },
}));

const member: Me = {
  username: "akshat",
  is_admin: false,
  email: "akshat@example.com",
  kind: "human",
  personal_workspace: "akshat",
  roles: {},
};
const admin: Me = { ...member, is_admin: true };

const workspace: WorkspaceInfo = {
  id: 1,
  name: "akshat",
  type: "personal",
  archived: false,
  promotes_to: null,
  promoted_from: null,
};

Element.prototype.scrollIntoView = () => {};

function stubBrowser(width: number) {
  vi.stubGlobal("innerWidth", width);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      return Response.json([]);
    }),
  );
}

beforeEach(() => {
  localStorage.setItem("devtools.theme", "light");
  stubBrowser(1440);
});

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.cookie = "sidebar_state=; path=/; max-age=0";
});

function page(name: string) {
  return (
    <>
      <PageHeader title={name} />
      <p>{name} page</p>
    </>
  );
}

function renderApp(me: Me | null, path: string) {
  return render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MeContext.Provider value={me}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/login" element={<p>login page</p>} />
              <Route element={<WorkspaceOrPublic />}>
                <Route element={<ShellOrBare />}>
                  <Route path="/docs/:tab" element={<p>docs body</p>} />
                  <Route path="/datasets" element={page("Datasets")} />
                  <Route path="/experiments" element={page("Experiments")} />
                  <Route path="/billing" element={page("Billing")} />
                </Route>
              </Route>
            </Routes>
          </MemoryRouter>
        </MeContext.Provider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function section(name: string) {
  return screen.getByRole("group", { name });
}

test("the sidebar groups evalkit's pages and puts extension items in their sections", async () => {
  renderApp(admin, "/datasets");
  await screen.findByText("Datasets page");

  expect(
    within(section("Evaluate"))
      .getAllByRole("link")
      .map((a) => a.textContent),
  ).toEqual(["Datasets", "Experiments", "Reviews"]);
  expect(
    within(section("Observe"))
      .getAllByRole("link")
      .map((a) => a.textContent),
  ).toEqual(["Runs", "Agents"]);
  expect(
    within(section("Workspace"))
      .getAllByRole("link")
      .map((a) => a.textContent),
  ).toEqual(["Settings", "Usage"]);
  expect(
    within(section("Admin"))
      .getAllByRole("link")
      .map((a) => a.textContent),
  ).toEqual(["Users", "Trace sources", "LLM providers", "Tenants"]);

  const groups = screen.getAllByRole("group");
  const billing = screen.getByRole("link", { name: "Billing" });
  const at = groups.findIndex((g) => g.contains(billing));
  expect(groups[at - 1]).toBe(section("Observe"));
  expect(groups[at + 1]).toBe(section("Workspace"));
});

test("only admins see the Admin section", async () => {
  const { unmount } = renderApp(member, "/datasets");
  await screen.findByText("Datasets page");
  expect(screen.queryByRole("group", { name: "Admin" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Tenants" })).toBeNull();
  unmount();

  renderApp(admin, "/datasets");
  await screen.findByText("Datasets page");
  expect(
    within(section("Admin")).getByRole("link", { name: "Users" }),
  ).toBeInTheDocument();
});

test("⌘K finds an extension page and opens it", async () => {
  const user = userEvent.setup();
  renderApp(member, "/datasets");
  await screen.findByText("Datasets page");

  await user.keyboard("{Meta>}k{/Meta}");
  const search = await screen.findByRole("dialog", { name: "Search" });
  await user.keyboard("Billing");
  expect(within(search).getByText("Go to Billing")).toBeInTheDocument();
  await user.keyboard("{Enter}");

  expect(await screen.findByText("Billing page")).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Search" })).toBeNull();
});

test("a signed-out page shows the public top bar, not the sidebar", () => {
  renderApp(null, "/docs/install");

  expect(screen.getByText("docs body")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
    "href",
    "/login?next=%2Fdocs%2Finstall",
  );
  expect(screen.getByRole("link", { name: "Docs" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Switch to dark mode" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Datasets" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Collapse sidebar" })).toBeNull();
});

test("a collapsed sidebar stays collapsed after a reload", async () => {
  const user = userEvent.setup();
  const { unmount } = renderApp(member, "/datasets");
  await user.click(
    await screen.findByRole("button", { name: "Collapse sidebar" }),
  );
  expect(
    screen.getByRole("button", { name: "Expand sidebar" }),
  ).toBeInTheDocument();
  unmount();

  renderApp(member, "/datasets");
  expect(
    await screen.findByRole("button", { name: "Expand sidebar" }),
  ).toBeInTheDocument();
});

test("the profile menu shows who is signed in, switches the theme and signs out", async () => {
  const user = userEvent.setup();
  renderApp(member, "/datasets");

  await user.click(await screen.findByRole("button", { name: /akshat/ }));
  const menu = await screen.findByRole("menu");
  expect(within(menu).getByText("akshat@example.com")).toBeInTheDocument();
  await user.click(within(menu).getByRole("menuitem", { name: "Dark mode" }));
  expect(document.documentElement).toHaveClass("dark");

  await user.click(screen.getByRole("button", { name: /akshat/ }));
  await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
  expect(await screen.findByText("login page")).toBeInTheDocument();
});

test("on a phone the page header opens the navigation, and picking a page closes it", async () => {
  stubBrowser(390);
  const user = userEvent.setup();
  renderApp(member, "/datasets");
  await screen.findByText("Datasets page");
  expect(screen.queryByRole("link", { name: "Experiments" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "Toggle Sidebar" }));
  const sheet = await screen.findByRole("dialog");
  await user.click(within(sheet).getByRole("link", { name: "Experiments" }));

  expect(await screen.findByText("Experiments page")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Experiments" })).toBeNull();
});
