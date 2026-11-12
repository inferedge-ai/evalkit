// @vitest-environment jsdom
import { User, type UserManager } from "oidc-client-ts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getApiToken, resetApiTokensForTest } from "./apiToken";
import {
  resetProvidersForTest,
  session,
  setSessionForTest,
  type OidcProviders,
} from "./oidc";

const API = "https://api.example.com";
const EVALKIT = "https://evalkit.example.com";

const idp: OidcProviders = {
  name: "Okta",
  issuer: "https://id.example.com",
  client_id: "0oa1b2c3",
  scopes: ["openid", "profile", "email", "offline_access"],
  authorize_params: { resource: EVALKIT },
  ui_resources: [API],
  authorization_endpoint: "https://id.example.com/authorize",
  token_endpoint: "https://id.example.com/token",
  device_authorization_endpoint: null,
};

type TokenAnswer = { status: number; body: Record<string, unknown> };

let tokenRequests: URLSearchParams[];
let heldLocks: string[];
let lockedDuringRequest: string[][];
let answer: TokenAnswer;

function serveIdp() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "none", oidc: idp });
      if (url.endsWith("/.well-known/openid-configuration"))
        return Response.json({
          issuer: idp.issuer,
          authorization_endpoint: idp.authorization_endpoint,
          token_endpoint: idp.token_endpoint,
        });
      if (url === idp.token_endpoint) {
        tokenRequests.push(new URLSearchParams(String(init?.body)));
        lockedDuringRequest.push([...heldLocks]);
        return Response.json(answer.body, { status: answer.status });
      }
      throw new Error(`unexpected fetch ${url}`);
    }),
  );
  vi.stubGlobal("navigator", {
    locks: {
      request: async (name: string, run: () => Promise<unknown>) => {
        heldLocks.push(name);
        try {
          return await run();
        } finally {
          heldLocks.pop();
        }
      },
    },
  });
}

async function manager(): Promise<UserManager> {
  return (await session()) as unknown as UserManager;
}

async function signIn(sub: string, refreshToken: string) {
  const now = Math.floor(Date.now() / 1000);
  await (
    await manager()
  ).storeUser(
    new User({
      access_token: `evalkit-token-${sub}`,
      refresh_token: refreshToken,
      token_type: "Bearer",
      profile: { sub, iss: idp.issuer, aud: idp.client_id, exp: now, iat: now },
      expires_at: now + 3600,
    }),
  );
}

function grant(accessToken: string, refreshToken: string, expiresIn = 3600) {
  answer = {
    status: 200,
    body: {
      access_token: accessToken,
      refresh_token: refreshToken,
      expires_in: expiresIn,
      token_type: "Bearer",
    },
  };
}

beforeEach(() => {
  tokenRequests = [];
  heldLocks = [];
  lockedDuringRequest = [];
  localStorage.clear();
  setSessionForTest(undefined);
  resetProvidersForTest();
  resetApiTokensForTest();
  serveIdp();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("getApiToken", () => {
  it("gets a token for the API with the refresh token, keeping evalkit's token and the rotated refresh token", async () => {
    await signIn("ann", "refresh-1");
    grant("api-token", "refresh-2");

    expect(await getApiToken(API)).toBe("api-token");

    expect(tokenRequests).toHaveLength(1);
    expect(tokenRequests[0].get("grant_type")).toBe("refresh_token");
    expect(tokenRequests[0].get("refresh_token")).toBe("refresh-1");
    expect(tokenRequests[0].getAll("resource")).toEqual([API]);
    const stored = await (await manager()).getUser();
    expect(stored?.access_token).toBe("evalkit-token-ann");
    expect(stored?.refresh_token).toBe("refresh-2");
  });

  it("asks the identity provider only while holding evalkit's refresh lock", async () => {
    await signIn("ann", "refresh-1");
    grant("api-token", "refresh-2");
    await getApiToken(API);
    expect(lockedDuringRequest).toEqual([["evalkit-oidc-refresh"]]);
  });

  it("reuses the token until a minute before it expires, then refreshes with the rotated token", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    await signIn("ann", "refresh-1");
    grant("api-token", "refresh-2");
    await getApiToken(API);
    expect(await getApiToken(API)).toBe("api-token");
    expect(tokenRequests).toHaveLength(1);

    vi.setSystemTime(Date.now() + (3600 - 59) * 1000);
    grant("api-token-2", "refresh-3");
    expect(await getApiToken(API)).toBe("api-token-2");
    expect(tokenRequests.map((r) => r.get("refresh_token"))).toEqual([
      "refresh-1",
      "refresh-2",
    ]);
  });

  it("is null when nobody is signed in through the identity provider", async () => {
    expect(await getApiToken(API)).toBeNull();
    expect(tokenRequests).toEqual([]);
  });

  it("is null when the identity provider refuses, and the person stays signed in to evalkit", async () => {
    await signIn("ann", "refresh-1");
    answer = { status: 400, body: { error: "invalid_target" } };

    expect(await getApiToken(API)).toBeNull();

    const stored = await (await manager()).getUser();
    expect(stored?.access_token).toBe("evalkit-token-ann");
    expect(stored?.refresh_token).toBe("refresh-1");
  });

  it("refuses an API outside ui_resources without asking the identity provider", async () => {
    await signIn("ann", "refresh-1");
    await expect(getApiToken("https://other.example.com")).rejects.toThrow(
      "not one of auth.oidc.ui_resources",
    );
    expect(tokenRequests).toEqual([]);
  });

  it("never hands one person's token to whoever signs in next", async () => {
    await signIn("ann", "refresh-1");
    grant("ann-api-token", "refresh-2");
    await getApiToken(API);

    await (await manager()).removeUser();
    expect(await getApiToken(API)).toBeNull();

    await signIn("bob", "refresh-9");
    grant("bob-api-token", "refresh-10");
    expect(await getApiToken(API)).toBe("bob-api-token");
    expect(tokenRequests.map((r) => r.get("refresh_token"))).toEqual([
      "refresh-1",
      "refresh-9",
    ]);
  });
});
