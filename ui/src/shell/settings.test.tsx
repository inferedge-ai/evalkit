// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { resetProvidersForTest, setSessionForTest } from "../auth/oidc";
import { SIDEBAR_COOKIE_NAME } from "../components/ui/sidebar";
import type { SettingsPage } from "../lib/extensions";
import { queryClient } from "../lib/query";
import type { Me } from "../lib/types";
import {
  renderMovedSettingsRoutes,
  renderSettingsRoutes,
} from "../settingsRoutes";
import {
  closePage,
  devWorkspace,
  failure,
  member,
  stubPasswordLogin,
  stubServer,
} from "../testFixtures";
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
  const { pathname, search } = useLocation();
  return <output aria-label="location">{pathname + search}</output>;
}

const where = () => screen.getByLabelText("location").textContent;

function openApp(me: Me, path: string) {
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MeContext.Provider value={me}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              {renderMovedSettingsRoutes()}
              <Route element={<WorkspaceOrPublic />}>
                <Route element={<ShellOrBare />}>
                  <Route
                    path="/experiments/:id"
                    element={<p>Experiment page</p>}
                  />
                  {renderSettingsRoutes()}
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
    {
      name: "workspace/members",
      label: "Hijack",
      element: <p>Hijack page</p>,
    },
  ];
});

afterEach(() => {
  closePage();
  setSessionForTest(undefined);
  resetProvidersForTest();
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
    "Workspaces",
    "Trace sources",
    "LLM providers",
    "Team",
  ]);
});

test.each([
  ["users", "Users", "People"],
  ["workspaces", "Workspaces", "Projects"],
])(
  "a wrapper's page can't take evalkit's name %s",
  async (name, heading, wrapperLabel) => {
    wrapper.organizationSettings = [
      {
        name,
        label: wrapperLabel,
        element: <p>Wrapper {wrapperLabel} page</p>,
      },
    ];
    openApp(admin, `/settings/${name}`);
    expect(
      await screen.findByRole("heading", { name: heading }),
    ).toBeInTheDocument();
    expect(screen.queryByText(`Wrapper ${wrapperLabel} page`)).toBeNull();
    expect(screen.queryByRole("link", { name: wrapperLabel })).toBeNull();
  },
);

test("an error on one workspace is gone after switching to another in Settings", async () => {
  stubServer((url) => {
    if (url.endsWith("/api/workspaces/dev/archive"))
      return failure("dev holds running experiments");
    if (url.endsWith("/api/workspaces?include_archived=true"))
      return Response.json([
        devWorkspace,
        { ...devWorkspace, id: 3, name: "prod" },
      ]);
  });
  openApp(admin, "/settings/workspace/members");
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Archive" }));
  expect(
    await screen.findByText("dev holds running experiments"),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "workspace" }));
  await user.click(await screen.findByRole("option", { name: /@prod/ }));
  expect(
    await screen.findByRole("heading", { name: "@prod" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("dev holds running experiments")).toBeNull();
});

test("a wrapper's page can't take a name another of its pages uses", async () => {
  wrapper.organizationSettings = [
    { name: "team", label: "Team", element: <p>Team page</p> },
    { name: "team", label: "Team again", element: <p>Second team page</p> },
    { name: "profile", label: "Org profile", element: <p>Org profile page</p> },
  ];
  openApp(admin, "/settings/profile");
  expect(await screen.findByText("Profile page")).toBeInTheDocument();
  expect(links("Account")).toEqual(["Profile"]);
  expect(links("Organization")).toEqual([
    "Users",
    "Workspaces",
    "Trace sources",
    "LLM providers",
    "Team",
  ]);
});

test("a wrapper's page name can't reach into evalkit's pages", async () => {
  openApp(admin, "/settings/workspace/members");
  await vi.waitFor(() => expect(where()).toBe("/settings/workspace/members"));
  expect(screen.queryByText("Hijack page")).toBeNull();
  expect(screen.queryByRole("link", { name: "Hijack" })).toBeNull();
});

test("Back to evalkit returns to the page Settings was opened from", async () => {
  openApp(writer, "/experiments/7?tab=runs");
  await screen.findByText("Experiment page");
  const user = await openSettingsFromMenu();
  await screen.findByText("Profile page");
  await user.click(screen.getByRole("link", { name: "Back to evalkit" }));
  expect(await screen.findByText("Experiment page")).toBeInTheDocument();
  expect(where()).toBe("/experiments/7?tab=runs");
});

test("after switching workspace in Settings, Back to evalkit opens the section, not the old workspace's page", async () => {
  stubServer((url) =>
    url.endsWith("/api/workspaces?include_archived=true")
      ? Response.json([devWorkspace, { ...devWorkspace, id: 3, name: "prod" }])
      : undefined,
  );
  openApp(writer, "/experiments/7");
  await screen.findByText("Experiment page");
  const user = await openSettingsFromMenu();
  await screen.findByText("Profile page");
  await user.click(screen.getByRole("button", { name: "workspace" }));
  await user.click(await screen.findByRole("option", { name: /@prod/ }));
  await user.click(screen.getByRole("link", { name: "Back to evalkit" }));
  await vi.waitFor(() => expect(where()).toBe("/experiments"));
});

test("a writer switches workspace settings tabs inside Settings", async () => {
  const user = userEvent.setup();
  openApp(writer, "/settings/workspace/evaluators");
  await user.click(await screen.findByRole("tab", { name: "Members" }));
  await vi.waitFor(() => expect(where()).toBe("/settings/workspace/members"));
  expect(
    screen.getByRole("link", { name: "Back to evalkit" }),
  ).toBeInTheDocument();
});

test.each([
  ["/admin/llm-providers", "/settings/llm-providers"],
  ["/admin/trace-sources", "/settings/trace-sources"],
  ["/users", "/settings/users"],
  ["/workspace", "/settings/workspace/evaluators"],
  ["/workspace/members", "/settings/workspace/members"],
])(
  "an old link to %s opens %s, with Back to evalkit going home",
  async (old, moved) => {
    openApp(admin, old);
    await vi.waitFor(() => expect(where()).toBe(moved));
    expect(
      await screen.findByRole("link", { name: "Back to evalkit" }),
    ).toHaveAttribute("href", "/");
  },
);

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
    within(search).getByText("Go to Settings › Organization › LLM providers"),
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
    within(search).getByText("Go to Settings › Account › Profile"),
  ).toBeInTheDocument();
  expect(
    within(search).queryByText("Go to Settings › Organization › LLM providers"),
  ).toBeNull();
});

async function searchPages(text: string) {
  const user = userEvent.setup();
  await user.keyboard("{Meta>}k{/Meta}");
  const search = await screen.findByRole("dialog", { name: "Search" });
  await user.type(
    within(search).getByPlaceholderText(
      "Pick a type, go to a page, or type an id",
    ),
    text,
  );
  return search;
}

test.each([
  [200, "/settings/users"],
  [503, "/users"],
])(
  "with password login off (providers %i), an admin finds no Users page at %s, in the sidebar or in ⌘K",
  async (status, path) => {
    stubPasswordLogin(status, "none");
    openApp(admin, path);
    expect(
      await screen.findByRole("heading", { name: "Not found" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Users" })).toBeNull();
    expect(where()).toBe("/settings/users");
    expect(links("Organization")).toEqual([
      "Workspaces",
      "Trace sources",
      "LLM providers",
      "Team",
    ]);
    const search = await searchPages("Settings");
    expect(
      within(search).getByText("Go to Settings › Organization › Workspaces"),
    ).toBeInTheDocument();
    expect(
      within(search).queryByText("Go to Settings › Organization › Users"),
    ).toBeNull();
  },
);

test("with password login for admins, an admin opens the Users page from the sidebar and ⌘K", async () => {
  stubPasswordLogin(200, "admin");
  openApp(admin, "/settings/users");
  expect(
    await screen.findByRole("heading", { name: "Users" }),
  ).toBeInTheDocument();
  expect(links("Organization")).toContain("Users");
  const search = await searchPages("Users");
  expect(
    within(search).getByText("Go to Settings › Organization › Users"),
  ).toBeInTheDocument();
});
