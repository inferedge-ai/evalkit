// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SIDEBAR_COOKIE_NAME } from "../components/ui/sidebar";
import type { SettingsPage } from "../lib/extensions";
import { queryClient } from "../lib/query";
import type { Me } from "../lib/types";
import { settingsRoutes } from "../settingsRoutes";
import { closePage, member, stubServer } from "../testFixtures";
import { MeContext } from "./capabilities";
import { ShellOrBare, WorkspaceOrPublic } from "./layout";
import { ThemeProvider } from "./theme";

const wrapper = vi.hoisted(() => ({
  routes: [],
  nav: [],
  accountSettings: [] as SettingsPage[],
  organizationSettings: [] as SettingsPage[],
}));
vi.mock("../extensions", () => ({ extensions: wrapper }));

Element.prototype.scrollIntoView = () => {};

const writer = member({ dev: ["writer"] });
const admin: Me = { ...writer, is_admin: true };

function Where() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

const where = () => screen.getByLabelText("location").textContent;

function openApp(me: Me, path: string) {
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MeContext.Provider value={me}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route element={<WorkspaceOrPublic />}>
                <Route element={<ShellOrBare />}>
                  <Route
                    path="/experiments/:id"
                    element={<p>Experiment page</p>}
                  />
                  {settingsRoutes()}
                </Route>
              </Route>
            </Routes>
            <Where />
          </MemoryRouter>
        </MeContext.Provider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

function links(section: string) {
  return within(screen.getByRole("group", { name: section }))
    .getAllByRole("link")
    .map((a) => a.textContent);
}

async function openSettingsFromMenu() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "akshat" }));
  await user.click(await screen.findByRole("menuitem", { name: "Settings" }));
  return user;
}

beforeEach(() => {
  localStorage.setItem("devtools.theme", "light");
  stubServer(() => undefined);
  wrapper.accountSettings = [
    { name: "profile", label: "Profile", element: <p>Profile page</p> },
  ];
  wrapper.organizationSettings = [
    { name: "team", label: "Team", element: <p>Team page</p> },
    { name: "users", label: "People", element: <p>Wrapper people page</p> },
  ];
});

afterEach(() => {
  closePage();
  localStorage.clear();
  document.cookie = `${SIDEBAR_COOKIE_NAME}=; path=/; max-age=0`;
});

test("a member opens Settings from the account menu and lands on the first page they can see", async () => {
  openApp(writer, "/experiments/7");
  await screen.findByText("Experiment page");
  await openSettingsFromMenu();
  expect(await screen.findByText("Profile page")).toBeInTheDocument();
  expect(where()).toBe("/settings/profile");
  expect(links("Account")).toEqual(["Profile"]);
  expect(links("Workspace")).toEqual(["@dev"]);
  expect(screen.queryByRole("group", { name: "Organization" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Team" })).toBeNull();
});

test("without a wrapper's pages, Settings lands on the workspace's settings", async () => {
  wrapper.accountSettings = [];
  openApp(writer, "/settings");
  await vi.waitFor(() =>
    expect(where()).toBe("/settings/workspace/evaluators"),
  );
  expect(screen.queryByRole("group", { name: "Account" })).toBeNull();
});

test("an admin's Organization section lists evalkit's pages, then the wrapper's, inside the Settings shell", async () => {
  openApp(admin, "/settings/team");
  expect(await screen.findByText("Team page")).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Back to evalkit" }),
  ).toBeInTheDocument();
  expect(links("Organization")).toEqual([
    "Users",
    "Trace sources",
    "LLM providers",
    "Team",
  ]);
});

test("a wrapper's page can't take a name evalkit uses", async () => {
  openApp(admin, "/settings/users");
  expect(
    await screen.findByRole("heading", { name: "Users" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Wrapper people page")).toBeNull();
  expect(screen.queryByRole("link", { name: "People" })).toBeNull();
});

test("Back to evalkit returns to the page Settings was opened from", async () => {
  openApp(writer, "/experiments/7");
  await screen.findByText("Experiment page");
  const user = await openSettingsFromMenu();
  await screen.findByText("Profile page");
  await user.click(screen.getByRole("link", { name: "Back to evalkit" }));
  expect(await screen.findByText("Experiment page")).toBeInTheDocument();
  expect(where()).toBe("/experiments/7");
});

test.each([
  ["/admin/llm-providers", "/settings/llm-providers"],
  ["/admin/trace-sources", "/settings/trace-sources"],
  ["/users", "/settings/users"],
  ["/workspace", "/settings/workspace/evaluators"],
  ["/workspace/members", "/settings/workspace/members"],
])("an old link to %s opens %s", async (old, moved) => {
  openApp(admin, old);
  await vi.waitFor(() => expect(where()).toBe(moved));
});

test("⌘K finds an admin's Settings pages and opens them in the Settings shell", async () => {
  const user = userEvent.setup();
  openApp(admin, "/experiments/7");
  await screen.findByText("Experiment page");
  await user.keyboard("{Meta>}k{/Meta}");
  const search = await screen.findByRole("dialog", { name: "Search" });
  await user.type(
    within(search).getByPlaceholderText(
      "Pick a type, go to a page, or type an id",
    ),
    "LLM",
  );
  expect(
    within(search).getByText("Go to Settings › LLM providers"),
  ).toBeInTheDocument();
  await user.keyboard("{Enter}");
  await vi.waitFor(() => expect(where()).toBe("/settings/llm-providers"));
  expect(
    screen.getByRole("link", { name: "Back to evalkit" }),
  ).toBeInTheDocument();
});

test("⌘K offers a member no admin Settings pages", async () => {
  const user = userEvent.setup();
  openApp(writer, "/experiments/7");
  await screen.findByText("Experiment page");
  await user.keyboard("{Meta>}k{/Meta}");
  const search = await screen.findByRole("dialog", { name: "Search" });
  await user.type(
    within(search).getByPlaceholderText(
      "Pick a type, go to a page, or type an id",
    ),
    "Settings",
  );
  expect(
    within(search).getByText("Go to Settings › Profile"),
  ).toBeInTheDocument();
  expect(
    within(search).queryByText("Go to Settings › LLM providers"),
  ).toBeNull();
});
