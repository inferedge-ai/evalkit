// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./app";
import { SignInLink } from "./auth/SignInLink";
import { resetProvidersForTest, setSessionForTest } from "./auth/oidc";
import { isSignedOut, logout } from "./lib/api";
import type { ExtensionRoute } from "./lib/extensions";
import { queryClient } from "./lib/query";
import type { Me } from "./lib/types";
import { ThemeProvider } from "./shell/theme";
import { closePage, member, stubServer } from "./testFixtures";

const wrapper = vi.hoisted(() => ({
  routes: [],
  publicRoutes: [] as ExtensionRoute[],
  landingPage: null as ReactElement | null,
  publicHeader: null as ReactElement | null,
  nav: [],
  accountSettings: [],
  organizationSettings: [],
}));
vi.mock("./extensions", () => ({ extensions: wrapper }));

Element.prototype.scrollIntoView = () => {};

const okta = {
  name: "Okta",
  issuer: "https://id.example.com",
  client_id: "0oa1b2c3",
  scopes: ["openid", "profile", "email"],
  authorize_params: {},
  authorization_endpoint: "https://id.example.com/oauth2/v1/authorize",
  token_endpoint: "https://id.example.com/oauth2/v1/token",
  device_authorization_endpoint: null,
  ui_resources: [],
};

const writer = member({ dev: ["writer"] });

function Landing() {
  return (
    <>
      <h1>Evals for your agents</h1>
      <SignInLink>Log in</SignInLink>
      <SignInLink prompt="create">Sign up</SignInLink>
    </>
  );
}

function Where() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="location">{pathname + search}</output>
      <button type="button" onClick={() => navigate(-1)}>
        Back
      </button>
    </>
  );
}

const where = () => screen.getByLabelText("location").textContent;

let redirects: unknown[];

function serve(me: Me | null) {
  stubServer(async (url) => {
    if (url.endsWith("/api/auth/me"))
      return me
        ? Response.json(me)
        : Response.json({ detail: "not signed in" }, { status: 401 });
    if (url.endsWith("/api/auth/providers"))
      return Response.json({ password: "none", oidc: okta });
    if (url.endsWith("/api/meta"))
      return Response.json({ detail: "no meta" }, { status: 404 });
    return undefined;
  });
}

function openApp(path: string) {
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <MemoryRouter initialEntries={[path]}>
          <App />
          <Where />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  localStorage.setItem("devtools.theme", "light");
  redirects = [];
  setSessionForTest({
    getUser: async () => null,
    signinRedirect: async (args) => {
      redirects.push(args);
      await new Promise(() => {});
    },
    signinCallback: async () => undefined,
    signinSilent: async () => null,
    removeUser: async () => {},
    requestApiToken: async () => null,
  });
  wrapper.landingPage = <Landing />;
  wrapper.publicHeader = null;
  wrapper.publicRoutes = [];
});

afterEach(() => {
  closePage();
  setSessionForTest(undefined);
  resetProvidersForTest();
  localStorage.clear();
});

test("a signed-out visitor at / sees the wrapper's landing page under the public navbar", async () => {
  serve(null);
  openApp("/");

  expect(await screen.findByText("Evals for your agents")).toBeInTheDocument();
  const navbar = screen.getByRole("banner");
  expect(within(navbar).getByRole("link", { name: "Docs" })).toBeVisible();
  expect(within(navbar).getByRole("link", { name: "Evalkit" })).toHaveAttribute(
    "href",
    "/",
  );
  expect(where()).toBe("/");
  expect(redirects).toEqual([]);
});

test("without a landing page, / sends a signed-out visitor to sign in", async () => {
  wrapper.landingPage = null;
  serve(null);
  openApp("/");

  expect(await screen.findByText("Signing you in…")).toBeInTheDocument();
  expect(where()).toBe("/login?next=%2F");
});

test("a signed-out deep link goes straight to sign-in and back, not to the landing page", async () => {
  serve(null);
  openApp("/experiments/12");

  expect(await screen.findByText("Signing you in…")).toBeInTheDocument();
  expect(screen.queryByText("Evals for your agents")).toBeNull();
  await vi.waitFor(() =>
    expect(redirects).toEqual([
      { state: "/experiments/12", redirectMethod: "replace" },
    ]),
  );
});

test("a signed-in person at / gets the workspace list, not the landing page", async () => {
  serve(writer);
  openApp("/");

  expect(
    await screen.findByRole("link", { name: /All datasets/ }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Evals for your agents")).toBeNull();
});

test("the sign-in page has no navbar, while the docs pages keep it", async () => {
  serve(null);
  openApp("/login");
  expect(
    await screen.findByRole("heading", { name: "Evalkit" }),
  ).toBeInTheDocument();
  expect(screen.queryByRole("banner")).toBeNull();
  expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Switch to/ })).toBeNull();
  closePage();

  serve(null);
  openApp("/docs/quickstart");
  const navbar = within(await screen.findByRole("banner"));
  expect(navbar.getByRole("link", { name: "Sign in" })).toBeVisible();
  expect(
    navbar.getByRole("button", { name: "Switch to dark mode" }),
  ).toBeVisible();
});

test("a wrapper's header replaces the public navbar on signed-out pages only", async () => {
  wrapper.publicHeader = (
    <header>
      <a href="/#pricing">Pricing</a>
    </header>
  );
  for (const path of ["/", "/docs/quickstart"]) {
    serve(null);
    openApp(path);
    const navbar = within(await screen.findByRole("banner"));
    expect(navbar.getByRole("link", { name: "Pricing" })).toBeVisible();
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "Sign in" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Switch to/ })).toBeNull();
    closePage();
  }

  serve(writer);
  openApp("/docs/quickstart");
  expect(await screen.findByRole("group", { name: "Evaluate" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Pricing" })).toBeNull();
});

test("the sign-in card's wordmark leads back to the wrapper's landing page", async () => {
  await logout();
  serve(null);
  openApp("/login");
  const heading = await screen.findByRole("heading", { name: "Evalkit" });

  await userEvent
    .setup()
    .click(within(heading).getByRole("link", { name: "Evalkit" }));

  expect(await screen.findByText("Evals for your agents")).toBeInTheDocument();
  expect(where()).toBe("/");
});

test("without a landing page, the sign-in card's wordmark is not a link", async () => {
  wrapper.landingPage = null;
  serve(null);
  openApp("/login");
  const heading = await screen.findByRole("heading", { name: "Evalkit" });
  expect(within(heading).queryByRole("link")).toBeNull();
});

test("a public page opens under the public navbar when signed out and in the app shell when signed in", async () => {
  wrapper.publicRoutes = [{ path: "/terms", element: <h1>Terms of use</h1> }];
  serve(null);
  openApp("/terms");

  expect(await screen.findByText("Terms of use")).toBeInTheDocument();
  expect(
    within(screen.getByRole("banner")).getByRole("link", { name: "Sign in" }),
  ).toBeVisible();
  expect(redirects).toEqual([]);
  closePage();

  serve(writer);
  openApp("/terms");
  expect(await screen.findByText("Terms of use")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: "Evaluate" })).toBeVisible();
});

test("Sign up on the landing page after a sign-out opens the provider's sign-up at once", async () => {
  await logout();
  serve(null);
  openApp("/");

  await userEvent
    .setup()
    .click(await screen.findByRole("link", { name: "Sign up" }));

  expect(await screen.findByText("Signing you in…")).toBeInTheDocument();
  await vi.waitFor(() =>
    expect(redirects).toEqual([
      { state: "/", prompt: "create", redirectMethod: "replace" },
    ]),
  );
  expect(isSignedOut()).toBe(true);
});

test("signing out lands on the landing page, and Back stops at Sign in again", async () => {
  serve(writer);
  openApp("/experiments");
  const user = userEvent.setup();

  await user.click(await screen.findByRole("button", { name: "akshat" }));
  await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));

  expect(await screen.findByText("Evals for your agents")).toBeInTheDocument();
  expect(where()).toBe("/");
  await vi.waitFor(() =>
    expect(queryClient.getQueryCache().getAll()).toEqual([]),
  );

  await user.click(screen.getByRole("button", { name: "Back" }));
  expect(
    await screen.findByRole("button", { name: "Sign in again" }),
  ).toBeInTheDocument();
  expect(where()).toBe("/login?next=%2Fexperiments");
});

test("evalkit's own pages win over a wrapper's public route at the same path", async () => {
  wrapper.publicRoutes = [
    { path: "/", element: <p>Wrapper home</p> },
    { path: "/datasets", element: <p>Wrapper datasets</p> },
  ];
  serve(writer);
  openApp("/");
  expect(
    await screen.findByRole("link", { name: /All datasets/ }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Wrapper home")).toBeNull();
  closePage();

  serve(writer);
  openApp("/datasets");
  expect(
    await screen.findByRole("heading", { name: "Datasets" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("Wrapper datasets")).toBeNull();
});
