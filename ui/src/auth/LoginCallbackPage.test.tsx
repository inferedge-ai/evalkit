// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { CallbackStatus, LoginCallbackPage } from "./LoginCallbackPage";
import { setSessionForTest } from "./oidc";

describe("CallbackStatus", () => {
  it("says it is completing the sign-in while no error is known", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <CallbackStatus error={null} />
      </MemoryRouter>,
    );
    expect(html).toContain("Completing sign-in…");
    expect(html).not.toContain("Try again");
  });

  it("shows the server's message and a way back on error", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <CallbackStatus error="user is archived" />
      </MemoryRouter>,
    );
    expect(html).toContain("user is archived");
    expect(html).toContain('href="/login"');
    expect(html).toContain("Try again");
  });
});

describe("LoginCallbackPage", () => {
  afterEach(() => {
    setSessionForTest(undefined);
    localStorage.clear();
  });

  it("shows a cancelled provider sign-in on the sign-in card, without the navbar", async () => {
    setSessionForTest({
      getUser: async () => null,
      signinRedirect: async () => {},
      signinCallback: async () => {
        throw new Error("The user cancelled");
      },
      signinSilent: async () => null,
      removeUser: async () => {},
      requestApiToken: async () => null,
    });
    render(
      <MemoryRouter initialEntries={["/login/callback"]}>
        <LoginCallbackPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("The user cancelled")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "evalkit" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Try again" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(screen.queryByRole("banner")).toBeNull();
  });
});
