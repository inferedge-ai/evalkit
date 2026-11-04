import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { LoginChoices } from "./LoginPage";
import type { Providers } from "./oidc";

const okta = {
  name: "Okta",
  issuer: "https://id.example.com",
  client_id: "0oa1b2c3",
  audience: "https://evalkit.example.com",
  scopes: ["openid", "profile", "email", "offline_access"],
  authorize_params: {},
  authorization_endpoint: "https://id.example.com/oauth2/v1/authorize",
  token_endpoint: "https://id.example.com/oauth2/v1/token",
  device_authorization_endpoint: null,
};

function render(providers: Providers): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <LoginChoices providers={providers} next="/" onSso={() => {}} />
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
