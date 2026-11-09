// @vitest-environment jsdom
import { render as renderDom, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { logout } from "../lib/api";
import { LoginCallbackPage } from "./LoginCallbackPage";
import { LoginChoices, LoginPage, runSso } from "./LoginPage";
import {
  resetProvidersForTest,
  setSessionForTest,
  type Providers,
} from "./oidc";

const okta = {
  name: "Okta",
  issuer: "https://id.example.com",
  client_id: "0oa1b2c3",
  scopes: ["openid", "profile", "email", "offline_access"],
  authorize_params: {},
  authorization_endpoint: "https://id.example.com/oauth2/v1/authorize",
  token_endpoint: "https://id.example.com/oauth2/v1/token",
  device_authorization_endpoint: null,
};

function render(providers: Providers): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <LoginChoices
        providers={providers}
        next="/"
        signedOut={false}
        onSso={() => Promise.resolve()}
      />
    </MemoryRouter>,
  );
}

describe("LoginChoices", () => {
  it("shows only the password form when the server has no IdP", () => {
    const html = render({ password: "all", oidc: null });
    expect(html).toContain("Username");
    expect(html).toContain("Sign in with your evalkit account.");
    expect(html).not.toContain("Continue with");
    expect(html).not.toContain("Admin sign-in");
  });

  it("shows the SSO button and a hidden admin form when password login is admin-only", () => {
    const html = render({ password: "admin", oidc: okta });
    expect(html).toContain("Continue with Okta");
    expect(html).toContain("Admin sign-in");
    expect(html).not.toContain("Username");
  });

  it("shows the SSO button above the open form when password login is for everyone", () => {
    const html = render({ password: "all", oidc: okta });
    expect(html).toContain("Continue with Okta");
    expect(html).toContain("Username");
    expect(html).not.toContain("Admin sign-in");
  });
});

describe("with passwords off", () => {
  function showLogin(onSso: () => Promise<void>, signedOut = false) {
    renderDom(
      <MemoryRouter>
        <LoginChoices
          providers={{ password: "none", oidc: okta }}
          next="/"
          signedOut={signedOut}
          onSso={onSso}
        />
      </MemoryRouter>,
    );
  }

  it("hands over to the provider at once, with no password form", async () => {
    const onSso = vi.fn(() => new Promise<void>(() => {}));
    showLogin(onSso);
    expect(await screen.findByText("Taking you to Okta…")).toBeTruthy();
    expect(onSso).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Username")).toBeNull();
    expect(screen.queryByText("Admin sign-in")).toBeNull();
  });

  it("waits for Sign in again after signing out", async () => {
    const onSso = vi.fn(() => new Promise<void>(() => {}));
    showLogin(onSso, true);
    expect(screen.getByText("You're signed out of evalkit.")).toBeTruthy();
    expect(onSso).not.toHaveBeenCalled();
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Sign in again" }));
    expect(screen.getByText("Taking you to Okta…")).toBeTruthy();
  });

  it("stops on the provider's error with Try again instead of starting over", async () => {
    const onSso = vi.fn(() => Promise.reject(new Error("Okta is down")));
    showLogin(onSso);
    expect(await screen.findByText("Okta is down")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(onSso).toHaveBeenCalledTimes(1);
  });
});

describe("LoginPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setSessionForTest(undefined);
    resetProvidersForTest();
    localStorage.clear();
  });

  function stubProvider(removeUser = async () => {}) {
    const redirects: unknown[] = [];
    resetProvidersForTest();
    setSessionForTest({
      getUser: async () => null,
      signinRedirect: async (args) => {
        redirects.push(args);
        await new Promise(() => {});
      },
      signinCallback: async () => ({
        access_token: "access",
        id_token: "id",
        state: "/experiments",
      }),
      signinSilent: async () => null,
      removeUser,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/api/auth/oidc/register")
          ? Response.json({ username: "akshat" })
          : Response.json({ password: "none", oidc: okta }),
      ),
    );
    return redirects;
  }

  function openLogin(path: string) {
    renderDom(
      <MemoryRouter initialEntries={[path]}>
        <LoginPage />
      </MemoryRouter>,
    );
  }

  it("hands over to the provider with the page the visitor asked for", async () => {
    const redirects = stubProvider();
    openLogin("/login?next=%2Fexperiments");
    await vi.waitFor(() =>
      expect(redirects).toEqual([
        { state: "/experiments", redirectMethod: "replace" },
      ]),
    );
  });

  it("waits for Sign in again after Sign out, even when another tab's expired session lands here", async () => {
    const redirects = stubProvider();
    await logout();
    openLogin("/login?next=%2Fexperiments");
    expect(
      await screen.findByRole("button", { name: "Sign in again" }),
    ).toBeTruthy();
    expect(redirects).toEqual([]);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Sign in again" }));
    expect(redirects).toEqual([
      { state: "/experiments", redirectMethod: "assign" },
    ]);
  });

  it("waits for Sign in again even when Sign out cannot remove its stored tokens", async () => {
    const redirects = stubProvider(async () => {
      throw new Error("storage unavailable");
    });
    await logout().catch(() => {});
    openLogin("/login?next=%2Fexperiments");
    expect(
      await screen.findByRole("button", { name: "Sign in again" }),
    ).toBeTruthy();
    expect(redirects).toEqual([]);
  });

  it("hands over at once again after the person signs back in", async () => {
    const redirects = stubProvider();
    await logout();
    renderDom(
      <MemoryRouter initialEntries={["/login/callback"]}>
        <Routes>
          <Route path="/login/callback" element={<LoginCallbackPage />} />
          <Route path="/experiments" element={<p>Experiments page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("Experiments page")).toBeTruthy();
    openLogin("/login?next=%2Fruns");
    await vi.waitFor(() =>
      expect(redirects).toEqual([
        { state: "/runs", redirectMethod: "replace" },
      ]),
    );
  });

  it("offers Retry, not a password form, when passwords are off and the provider is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { detail: "identity provider unreachable", password: "none" },
          { status: 503 },
        ),
      ),
    );
    renderDom(
      <MemoryRouter initialEntries={["/login"]}>
        <LoginPage />
      </MemoryRouter>,
    );
    expect(
      await screen.findByText("identity provider unreachable"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(screen.queryByLabelText("Username")).toBeNull();
  });
});

describe("password sign-in", () => {
  afterEach(() => vi.unstubAllGlobals());

  async function signIn(username: string, password: string) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const body = JSON.parse(String(init?.body ?? "{}"));
        if (
          url.endsWith("/api/auth/login") &&
          body.username === "admin" &&
          body.password === "s3cret"
        )
          return Response.json({ token: "token-1" });
        return Response.json(
          { detail: "invalid username or password" },
          { status: 401 },
        );
      }),
    );
    renderDom(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route
            path="/login"
            element={
              <LoginChoices
                providers={{ password: "all", oidc: null }}
                next="/datasets"
                signedOut={false}
                onSso={() => Promise.resolve()}
              />
            }
          />
          <Route path="/datasets" element={<p>Datasets page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Username"), username);
    await user.type(screen.getByLabelText("Password"), password);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
  }

  it("lands on the page the user asked for", async () => {
    await signIn("admin", "s3cret");
    expect(await screen.findByText("Datasets page")).toBeTruthy();
  });

  it("stays on the form with the server's message when the password is wrong", async () => {
    await signIn("admin", "wrong");
    expect(
      await screen.findByText("invalid username or password"),
    ).toBeTruthy();
    expect(screen.queryByText("Datasets page")).toBeNull();
  });
});

describe("runSso", () => {
  it("goes busy and stays busy while the redirect starts", async () => {
    const calls: unknown[] = [];
    runSso(
      () => new Promise(() => {}),
      (b) => calls.push(["busy", b]),
      (m) => calls.push(["error", m]),
    );
    await Promise.resolve();
    expect(calls).toEqual([
      ["busy", true],
      ["error", null],
    ]);
  });

  it("shows the error and re-enables the button when the start fails", async () => {
    const calls: unknown[] = [];
    runSso(
      () => Promise.reject(new Error("IdP unreachable")),
      (b) => calls.push(["busy", b]),
      (m) => calls.push(["error", m]),
    );
    await new Promise((r) => setTimeout(r, 0));
    expect(calls).toEqual([
      ["busy", true],
      ["error", null],
      ["error", "IdP unreachable"],
      ["busy", false],
    ]);
  });
});
