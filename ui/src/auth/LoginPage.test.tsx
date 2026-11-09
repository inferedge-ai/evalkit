// @vitest-environment jsdom
import { render as renderDom, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginChoices, runSso } from "./LoginPage";
import type { Providers } from "./oidc";

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
