import { afterEach, describe, expect, it, vi } from "vitest";
import {
  loadProviders,
  resetProvidersForTest,
  session,
  setSessionForTest,
  userManagerSettings,
  type OidcProviders,
  type Providers,
} from "./oidc";

const okta: OidcProviders = {
  name: "Okta",
  issuer: "https://id.example.com",
  client_id: "0oa1b2c3",
  audience: "https://evalkit.example.com",
  scopes: ["openid", "profile", "email", "offline_access"],
  authorize_params: { resource: "https://evalkit.example.com" },
  authorization_endpoint: "https://id.example.com/oauth2/v1/authorize",
  token_endpoint: "https://id.example.com/oauth2/v1/token",
  device_authorization_endpoint: null,
};

function stubProviders(body: Providers): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => body,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetProvidersForTest();
  setSessionForTest(undefined);
});

describe("loadProviders", () => {
  it("fetches /api/auth/providers once and caches the answer", async () => {
    const fetchMock = stubProviders({ password: "admin", oidc: okta });
    const first = await loadProviders();
    const second = await loadProviders();
    expect(first.oidc?.name).toBe("Okta");
    expect(second).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/providers");
  });

  it("does not cache a failed fetch", async () => {
    const failing = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: "identity provider unreachable" }),
    }));
    vi.stubGlobal("fetch", failing);
    await expect(loadProviders()).rejects.toThrow(
      "identity provider unreachable",
    );
    stubProviders({ password: "all", oidc: null });
    expect((await loadProviders()).password).toBe("all");
  });
});

describe("userManagerSettings", () => {
  it("maps the providers answer onto oidc-client-ts settings", () => {
    const s = userManagerSettings(okta, "https://evalkit.example.com");
    expect(s.authority).toBe("https://id.example.com");
    expect(s.client_id).toBe("0oa1b2c3");
    expect(s.redirect_uri).toBe("https://evalkit.example.com/login/callback");
    expect(s.scope).toBe("openid profile email offline_access");
    expect(s.response_type).toBe("code");
    expect(s.extraQueryParams).toEqual({
      resource: "https://evalkit.example.com",
    });
    expect(s.automaticSilentRenew).toBe(false);
  });
});

describe("session", () => {
  it("is null when the server has no IdP", async () => {
    stubProviders({ password: "all", oidc: null });
    expect(await session()).toBeNull();
  });

  it("returns the injected session under test", async () => {
    const fake = {
      getUser: async () => null,
      signinRedirect: async () => {},
      signinCallback: async () => undefined,
      signinSilent: async () => null,
      removeUser: async () => {},
    };
    setSessionForTest(fake);
    expect(await session()).toBe(fake);
  });
});
