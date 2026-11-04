import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  NotFoundError,
  apiFetch,
  bearer,
  detailText,
  isMissing,
  logout,
  noteServerDate,
  register,
  serverNow,
  setToken,
} from "./api";
import { setSessionForTest, type OidcSession } from "../auth/oidc";

function fakeSession(
  user: { access_token: string; id_token?: string; expires_in?: number } | null,
  renewed: { access_token: string; expires_in?: number } | null = null,
): OidcSession & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    getUser: async () => {
      calls.push("getUser");
      return user;
    },
    signinSilent: async () => {
      calls.push("signinSilent");
      return renewed;
    },
    signinRedirect: async () => {
      calls.push("signinRedirect");
    },
    signinCallback: async () => undefined,
    removeUser: async () => {
      calls.push("removeUser");
    },
  };
}

afterEach(() => {
  setToken(null);
  setSessionForTest(undefined);
  vi.unstubAllGlobals();
});

describe("api errors", () => {
  it("formats validation details instead of [object Object]", () => {
    expect(
      detailText([
        { loc: ["path", "id"], msg: "Input should be a valid integer" },
        { msg: "second" },
      ]),
    ).toBe("Input should be a valid integer; second");
    expect(detailText("plain")).toBe("plain");
    expect(detailText({ a: 1 })).toBe('{"a":1}');
  });

  it("treats 400, 404 and 422 as a dead link", () => {
    expect(isMissing(new NotFoundError("x"))).toBe(true);
    expect(isMissing(new ApiError(422, "x"))).toBe(true);
    expect(isMissing(new ApiError(400, "x"))).toBe(true);
    expect(isMissing(new ApiError(500, "x"))).toBe(false);
    expect(isMissing(new Error("x"))).toBe(false);
  });
});

describe("serverNow", () => {
  it("follows the server's clock from its Date header", () => {
    noteServerDate(new Date(Date.now() + 60_000).toUTCString());
    expect(serverNow() - Date.now()).toBeGreaterThan(58_000);
    noteServerDate("not a date");
    expect(serverNow() - Date.now()).toBeGreaterThan(58_000);
    noteServerDate(new Date().toUTCString());
    expect(Math.abs(serverNow() - Date.now())).toBeLessThan(1_500);
  });
});

describe("bearer", () => {
  it("prefers the password token", async () => {
    setToken("evalkit-token");
    setSessionForTest(fakeSession({ access_token: "idp", expires_in: 3600 }));
    expect(await bearer()).toBe("evalkit-token");
  });

  it("hands out the IdP access token when it has more than a minute left", async () => {
    const s = fakeSession({ access_token: "idp", expires_in: 3600 });
    setSessionForTest(s);
    expect(await bearer()).toBe("idp");
    expect(s.calls).toEqual(["getUser"]);
  });

  it("refreshes when the access token expires within a minute", async () => {
    const s = fakeSession(
      { access_token: "old", expires_in: 30 },
      { access_token: "new", expires_in: 3600 },
    );
    setSessionForTest(s);
    expect(await bearer()).toBe("new");
    expect(s.calls).toEqual(["getUser", "signinSilent"]);
  });

  it("is null when the refresh is refused", async () => {
    setSessionForTest(
      fakeSession({ access_token: "old", expires_in: 0 }, null),
    );
    expect(await bearer()).toBeNull();
  });

  it("is null with no session and no token", async () => {
    setSessionForTest(null);
    expect(await bearer()).toBeNull();
  });
});

describe("apiFetch", () => {
  it("sends the bearer and clears credentials on 401", async () => {
    const s = fakeSession({ access_token: "idp", expires_in: 3600 });
    setSessionForTest(s);
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: false,
      status: 401,
      headers: new Headers(),
      json: async () => ({ detail: "token expired" }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const resp = await apiFetch("/auth/me");
    expect(resp.status).toBe(401);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer idp");
    expect(s.calls).toContain("removeUser");
  });
});

describe("register", () => {
  it("posts the ID token with the access token as bearer", async () => {
    setSessionForTest(fakeSession({ access_token: "idp", expires_in: 3600 }));
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({
        username: "akshat",
        is_admin: false,
        email: "akshat@example.com",
        kind: "human",
        created: true,
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const me = await register("id-token");
    expect(me.username).toBe("akshat");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/auth/oidc/register");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ id_token: "id-token" });
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer idp");
  });
});

describe("logout", () => {
  it("posts to the server for a password session", async () => {
    setToken("evalkit-token");
    setSessionForTest(null);
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
      ok: true,
      status: 200,
      headers: new Headers(),
      json: async () => ({ ok: true }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    await logout();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/logout");
    expect(await bearer()).toBeNull();
  });

  it("removes the IdP user for an SSO session without calling the server", async () => {
    const s = fakeSession({ access_token: "idp", expires_in: 3600 });
    setSessionForTest(s);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await logout();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(s.calls).toContain("removeUser");
  });
});
