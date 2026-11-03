# Web UI SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The web UI signs a person in through the configured OIDC identity provider as a public OAuth client, registers them with evalkit once, and sends the IdP access token on every API call, while admin password login keeps working.

**Architecture:** `oidc-client-ts` owns the authorization code + PKCE flow, token storage and refresh. A small `auth/oidc.ts` module reads `/api/auth/providers` once and builds the `UserManager`. `lib/api.ts` replaces its stored token string with a `bearer()` function that prefers the admin password token and otherwise hands out the IdP access token, refreshing it when it is within a minute of expiry. Two pages change: `/login` grows an SSO button, and a new `/login/callback` finishes the code exchange and calls the register route. Components that need the DOM are split into a presentational part rendered with `renderToStaticMarkup` in tests and a thin effect wrapper, because the vitest setup has no DOM environment.

**Tech Stack:** React 19, react-router-dom 7, TypeScript 6 strict, vitest 4 (node environment, `renderToStaticMarkup`), `oidc-client-ts` 3.5.0, prettier via pre-commit.

**Spec:** `docs/superpowers/specs/2026-11-03-oidc-sso-and-tenancy-design.md`, section "Web UI", plus the UI rows of "Docs, changelog, versioning and tests". Interface contract: `docs/superpowers/plans/2026-11-04-oidc-sso-and-tenancy/00-overview.md`.

## Global Constraints

- Plans 1 and 2 are merged. The server provides `GET /api/auth/providers` → `{password: "all" | "admin", oidc: null | {name, issuer, client_id, audience, scopes, authorize_params, authorization_endpoint, token_endpoint, device_authorization_endpoint}}`; `POST /api/auth/oidc/register` (bearer = access token, body `{id_token}`) → `{username, is_admin, email, kind, created}` or 409 / 401 with `detail`; `GET /api/auth/me` includes `email: string | null` and `kind: "human" | "machine"`; `POST /api/auth/login` answers 403 for non-admins when `password` is `"admin"`.
- `oidc-client-ts` is pinned to `3.5.0` (verified: `npm view oidc-client-ts version`). Verified API used: `new UserManager(settings)`, `settings.authority`, `client_id`, `redirect_uri`, `scope`, `response_type`, `extraQueryParams`, `userStore: new WebStorageStateStore({ store: localStorage })`, `automaticSilentRenew`, `userManager.getUser(): Promise<User | null>`, `signinRedirect({ state })`, `signinCallback(): Promise<User | undefined>`, `signinSilent(): Promise<User | null>`, `removeUser()`, `User.access_token`, `User.id_token?`, `User.expires_in?: number`, `User.state?: unknown`.
- The vitest config is the default: node environment, no jsdom, no setup file. Tests render with `renderToStaticMarkup` inside `MemoryRouter` (see `ui/src/shell/NotFound.test.tsx`) or test pure functions. Never construct a real `UserManager` in a test; inject the `OidcSession` fake defined in Task 1.
- Repo rules: no browser `alert()`, `confirm()` or `prompt()`; no new comments or docstrings; keep the existing login form behaviour (autofocus on username, `required`, busy state text "Signing in…", error text in `text-err-text`); `localStorage` may be absent under test, as `lib/api.ts` already allows.
- Run tests with `cd ui && npx vitest run <file>`; type-check with `cd ui && npx tsc -b`; build with `cd ui && npm run build`. Pre-commit runs prettier on `ui/`; if a commit is rejected, run `cd ui && npm run format` and commit again.
- Stage explicit paths. Every commit message ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Branch: `oauth` (the spec's PR branch) or a branch off it; the PR targets `develop` and is opened as a draft.

---

## File structure

| File | Responsibility |
| --- | --- |
| `ui/src/auth/oidc.ts` (new) | Load `/api/auth/providers` once; the `OidcSession` interface; build the `UserManager`; `session()` accessor with a test override. No React. |
| `ui/src/auth/oidc.test.ts` (new) | Providers caching, `UserManager` settings, session accessor. |
| `ui/src/lib/api.ts` (modify) | `bearer()`, 401 handling, `register()`, `logout()` deciding between password and SSO. |
| `ui/src/lib/api.test.ts` (modify) | `bearer()` precedence and refresh; 401 clears credentials. |
| `ui/src/lib/types.ts` (modify) | `Me` gains `email` and `kind`. |
| `ui/src/auth/LoginPage.tsx` (modify) | `LoginForm` extracted from today's page; `LoginChoices` presentational; `LoginPage` loads providers and renders `LoginChoices`. |
| `ui/src/auth/LoginPage.test.tsx` (new) | The three states of `LoginChoices`. |
| `ui/src/auth/completeSignIn.ts` (new) | Pure function: callback → register → `next`. |
| `ui/src/auth/completeSignIn.test.ts` (new) | Success, 409, archived, missing ID token. |
| `ui/src/auth/LoginCallbackPage.tsx` (new) | Effect wrapper around `completeSignIn`; `CallbackStatus` presentational. |
| `ui/src/auth/LoginCallbackPage.test.tsx` (new) | `CallbackStatus` renders the two states. |
| `ui/src/App.tsx` (modify) | Route `/login/callback` outside `AuthGate`. |
| `ui/src/shell/Shell.tsx` (modify) | Sign out uses `logout()`; account chip shows email. |
| `ui/src/shell/Shell.test.tsx` (new) | `AccountChip` renders email for SSO users. |
| `CHANGELOG.md`, `docs/user/login.md` (modify) | Entry under Unreleased; the UI paragraph of the SSO section. |

---

### Task 1: The oidc module: providers, session interface, UserManager

**Files:**
- Create: `ui/src/auth/oidc.ts`
- Create: `ui/src/auth/oidc.test.ts`
- Modify: `ui/package.json` (dependency)

**Interfaces:**
- Consumes: `GET /api/auth/providers` (Plan 2).
- Produces:
  - `type Providers = { password: "all" | "admin"; oidc: OidcProviders | null }`
  - `type OidcProviders = { name: string; issuer: string; client_id: string; audience: string; scopes: string[]; authorize_params: Record<string, string>; authorization_endpoint: string; token_endpoint: string; device_authorization_endpoint: string | null }`
  - `interface SessionUser { access_token: string; id_token?: string; expires_in?: number; state?: unknown }`
  - `interface OidcSession { getUser(): Promise<SessionUser | null>; signinRedirect(args: { state: unknown }): Promise<void>; signinCallback(): Promise<SessionUser | undefined>; signinSilent(): Promise<SessionUser | null>; removeUser(): Promise<void> }`
  - `loadProviders(): Promise<Providers>` (cached), `resetProvidersForTest(): void`
  - `userManagerSettings(oidc: OidcProviders, origin: string): UserManagerSettings`
  - `session(): Promise<OidcSession | null>` (null when `providers.oidc` is null), `setSessionForTest(s: OidcSession | null | undefined): void` (`undefined` restores the real one)

- [ ] **Step 1: Install the library**

```bash
cd ui && npm install oidc-client-ts@3.5.0
```

Check `ui/package.json` now lists `"oidc-client-ts": "^3.5.0"` under `dependencies` and `package-lock.json` changed.

- [ ] **Step 2: Write the failing tests**

`ui/src/auth/oidc.test.ts`:

```ts
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
    await expect(loadProviders()).rejects.toThrow("identity provider unreachable");
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ui && npx vitest run src/auth/oidc.test.ts`
Expected: FAIL with `Failed to resolve import "./oidc"`.

- [ ] **Step 4: Write the module**

`ui/src/auth/oidc.ts`:

```ts
import {
  UserManager,
  WebStorageStateStore,
  type UserManagerSettings,
} from "oidc-client-ts";

const API = import.meta.env.VITE_API_BASE_URL ?? "";

export type OidcProviders = {
  name: string;
  issuer: string;
  client_id: string;
  audience: string;
  scopes: string[];
  authorize_params: Record<string, string>;
  authorization_endpoint: string;
  token_endpoint: string;
  device_authorization_endpoint: string | null;
};

export type Providers = {
  password: "all" | "admin";
  oidc: OidcProviders | null;
};

export interface SessionUser {
  access_token: string;
  id_token?: string;
  expires_in?: number;
  state?: unknown;
}

export interface OidcSession {
  getUser(): Promise<SessionUser | null>;
  signinRedirect(args: { state: unknown }): Promise<void>;
  signinCallback(): Promise<SessionUser | undefined>;
  signinSilent(): Promise<SessionUser | null>;
  removeUser(): Promise<void>;
}

let providersPromise: Promise<Providers> | null = null;

export function loadProviders(): Promise<Providers> {
  if (providersPromise === null) {
    providersPromise = fetch(`${API}/api/auth/providers`).then(async (resp) => {
      const body = await resp
        .json()
        .catch(() => ({}) as Record<string, unknown>);
      if (!resp.ok) {
        providersPromise = null;
        throw new Error(String(body.detail ?? "cannot load sign-in options"));
      }
      return body as Providers;
    });
  }
  return providersPromise;
}

export function resetProvidersForTest(): void {
  providersPromise = null;
}

export function userManagerSettings(
  oidc: OidcProviders,
  origin: string,
): UserManagerSettings {
  const store =
    typeof localStorage !== "undefined" ? localStorage : undefined;
  return {
    authority: oidc.issuer,
    client_id: oidc.client_id,
    redirect_uri: `${origin}/login/callback`,
    scope: oidc.scopes.join(" "),
    response_type: "code",
    extraQueryParams: oidc.authorize_params,
    userStore: new WebStorageStateStore({ store }),
    automaticSilentRenew: false,
  };
}

let manager: OidcSession | null | undefined;
let override: OidcSession | null | undefined;

export async function session(): Promise<OidcSession | null> {
  if (override !== undefined) return override;
  if (manager === undefined) {
    const providers = await loadProviders();
    manager =
      providers.oidc === null
        ? null
        : new UserManager(
            userManagerSettings(providers.oidc, window.location.origin),
          );
  }
  return manager;
}

export function setSessionForTest(s: OidcSession | null | undefined): void {
  override = s;
  manager = undefined;
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ui && npx vitest run src/auth/oidc.test.ts`
Expected: PASS, 5 tests.

- [ ] **Step 6: Type-check**

Run: `cd ui && npx tsc -b`
Expected: no output. If `UserManager` is reported as not assignable to `OidcSession`, the structural interface drifted from the library's `User` type; align `SessionUser` with `User` (the fields above are all declared on `User` in 3.5.0).

- [ ] **Step 7: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/src/auth/oidc.ts ui/src/auth/oidc.test.ts
git commit -m "UI: oidc module with providers cache and UserManager settings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `bearer()`, 401 handling, `register()` and `logout()` in `lib/api.ts`

**Files:**
- Modify: `ui/src/lib/api.ts`
- Modify: `ui/src/lib/api.test.ts`
- Modify: `ui/src/lib/types.ts` (the `Me` interface, lines 2–8)

**Interfaces:**
- Consumes: `session()`, `OidcSession`, `SessionUser` from Task 1.
- Produces:
  - `bearer(): Promise<string | null>`
  - `apiFetch` unchanged signature; sends `Authorization` from `bearer()`; on 401 clears both credentials and, for any path other than `/auth/me`, navigates to `/login?next=<path+search>`.
  - `register(idToken: string): Promise<Me>`
  - `logout(): Promise<void>` (password: `POST /auth/logout` then clears; SSO: `removeUser()`)
  - `setToken`, `login` unchanged; `hasToken` removed (nothing imports it).
  - `Me` gains `email: string | null` and `kind: "human" | "machine"`.
  - `signOutLocally(): Promise<void>` clears both credentials without network.

- [ ] **Step 1: Write the failing tests**

Append to `ui/src/lib/api.test.ts` (keep the existing imports and tests; add `afterEach`, `vi` to the vitest import and the new imports):

```ts
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
    setSessionForTest(fakeSession({ access_token: "old", expires_in: 0 }, null));
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
    const fetchMock = vi.fn(async () => ({
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
    const fetchMock = vi.fn(async () => ({
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
    const fetchMock = vi.fn(async () => ({
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ui && npx vitest run src/lib/api.test.ts`
Expected: FAIL with `"bearer" is not exported` (and `register`, `logout` shape errors).

- [ ] **Step 3: Change `lib/api.ts`**

Replace the top of the file (through `hasToken`) and the `apiFetch`, `login`, `logout` functions. The rest stays. The file becomes:

```ts
import { session } from "../auth/oidc";
import type { Me } from "./types";

const API = import.meta.env.VITE_API_BASE_URL ?? "";

const TOKEN_KEY = "evalkit.token";

const store: Storage | null =
  typeof localStorage !== "undefined" ? localStorage : null;

let token: string | null = store?.getItem(TOKEN_KEY) ?? null;

export function setToken(value: string | null): void {
  token = value;
  if (value === null) store?.removeItem(TOKEN_KEY);
  else store?.setItem(TOKEN_KEY, value);
}

const REFRESH_WINDOW_SECONDS = 60;

export async function bearer(): Promise<string | null> {
  if (token !== null) return token;
  const s = await session();
  if (s === null) return null;
  let user = await s.getUser();
  if (user === null) return null;
  if ((user.expires_in ?? 0) < REFRESH_WINDOW_SECONDS) {
    user = await s.signinSilent();
  }
  return user?.access_token ?? null;
}

export async function signOutLocally(): Promise<void> {
  setToken(null);
  const s = await session();
  if (s !== null) await s.removeUser();
}

// ... ApiError, NotFoundError, isNotFound, isMissing, isExperimentId,
// detailText, serverNow, noteServerDate stay exactly as they are ...

export async function apiFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  const b = await bearer();
  if (b) headers.set("Authorization", `Bearer ${b}`);
  const resp = await fetch(`${API}/api${path}`, { ...init, headers });
  noteServerDate(resp.headers.get("Date"));
  if (resp.status === 401) {
    await signOutLocally();
    if (path !== "/auth/me" && typeof window !== "undefined") {
      const next = encodeURIComponent(
        window.location.pathname + window.location.search,
      );
      window.location.assign(`/login?next=${next}`);
    }
  }
  return resp;
}

// ... api, apiJson, apiBlobUrl, downloadFile, qs stay exactly as they are ...

export async function login(username: string, password: string): Promise<void> {
  const resp = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const body = await resp.json().catch(() => ({}) as Record<string, unknown>);
  if (!resp.ok)
    throw new ApiError(resp.status, String(body.detail ?? "login failed"));
  setToken(String(body.token));
}

export async function register(idToken: string): Promise<Me> {
  return apiJson<Me>("/auth/oidc/register", "POST", { id_token: idToken });
}

export async function logout(): Promise<void> {
  if (token !== null) {
    await apiFetch("/auth/logout", { method: "POST" }).catch(() => {});
  }
  await signOutLocally();
}
```

Delete `hasToken` (grep confirms no importers). Keep the two existing explanatory comments on `apiBlobUrl` and `downloadFile`; remove the `// Absent under test / SSR` comment only if you touch that line (the code above drops it, which is allowed since the line is rewritten).

In `ui/src/lib/types.ts` change `Me` to:

```ts
export interface Me {
  username: string;
  is_admin: boolean;
  email: string | null;
  kind: "human" | "machine";
  personal_workspace: string | null;
  // workspace name → the roles this user holds there (writer, reviewer).
  roles: Record<string, WorkspaceRole[]>;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ui && npx vitest run src/lib/api.test.ts`
Expected: PASS. The `apiFetch` 401 test reaches `signOutLocally` and does not touch `window` because `typeof window === "undefined"` under node.

- [ ] **Step 5: Type-check and fix `Me` consumers**

Run: `cd ui && npx tsc -b`
Expected: errors only where a `Me` literal is built without the two new fields (tests or fixtures that construct `Me`). Add `email: null, kind: "human"` to each such literal. Re-run until clean.

- [ ] **Step 6: Commit**

```bash
git add ui/src/lib/api.ts ui/src/lib/api.test.ts ui/src/lib/types.ts
git commit -m "UI: bearer() prefers the password token, else the IdP access token with refresh

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The login page: SSO button, admin sign-in link, three states

**Files:**
- Modify: `ui/src/auth/LoginPage.tsx`
- Create: `ui/src/auth/LoginPage.test.tsx`

**Interfaces:**
- Consumes: `loadProviders`, `session`, `Providers` (Task 1); `login` (Task 2).
- Produces:
  - `LoginForm({ next })`: today's form, extracted unchanged.
  - `LoginChoices({ providers, next, onSso })`: presentational; `onSso` starts the redirect.
  - `LoginPage()`: loads providers, renders `LoginChoices` with `onSso = () => session().then(s => s!.signinRedirect({ state: next }))`.

- [ ] **Step 1: Write the failing tests**

`ui/src/auth/LoginPage.test.tsx`:

```tsx
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ui && npx vitest run src/auth/LoginPage.test.tsx`
Expected: FAIL with `"LoginChoices" is not exported`.

- [ ] **Step 3: Rewrite `LoginPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login } from "../lib/api";
import { loadProviders, session, type Providers } from "./oidc";

function LoginForm({ next }: { next: string }) {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      navigate(next, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <label className="mb-3 block text-micro font-semibold tracking-wide text-ink-2 uppercase">
        Username
        <input
          className="input mt-1 block w-full"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          required
        />
      </label>
      <label className="mb-5 block text-micro font-semibold tracking-wide text-ink-2 uppercase">
        Password
        <input
          className="input mt-1 block w-full"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {error && <div className="mb-3 text-meta text-err-text">{error}</div>}
      <button className="btn btn-primary w-full justify-center" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

export function LoginChoices({
  providers,
  next,
  onSso,
}: {
  providers: Providers;
  next: string;
  onSso: () => void;
}) {
  const [adminOpen, setAdminOpen] = useState(false);
  const oidc = providers.oidc;
  const formOpen = oidc === null || providers.password === "all" || adminOpen;
  return (
    <div className="card w-84 p-6">
      <h1 className="page-title">Evalkit</h1>
      <p className="mt-1 mb-5 text-meta text-ink-3">
        {oidc === null
          ? "Sign in with your evalkit account."
          : `Sign in with your ${oidc.name} account.`}
      </p>
      {oidc !== null && (
        <button
          type="button"
          className="btn btn-primary mb-4 w-full justify-center"
          onClick={onSso}
        >
          Continue with {oidc.name}
        </button>
      )}
      {formOpen ? (
        <LoginForm next={next} />
      ) : (
        <button
          type="button"
          className="text-meta text-ink-3 underline"
          onClick={() => setAdminOpen(true)}
        >
          Admin sign-in
        </button>
      )}
    </div>
  );
}

export function LoginPage() {
  const [params] = useSearchParams();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rawNext = params.get("next") ?? "/";
  const next = rawNext.startsWith("/login") ? "/" : rawNext;

  useEffect(() => {
    loadProviders().then(setProviders, (e) => setError(String(e)));
  }, []);

  const startSso = async () => {
    const s = await session();
    if (s === null) return;
    await s.signinRedirect({ state: next });
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas">
      {error ? (
        <div className="card w-84 p-6 text-meta text-err-text">{error}</div>
      ) : providers === null ? (
        <div className="pane-status">Loading sign-in options…</div>
      ) : (
        <LoginChoices providers={providers} next={next} onSso={startSso} />
      )}
    </div>
  );
}
```

The form keeps its `autoFocus`, `required`, busy text and error styling. With `password === "all"` the form opens under the button and the admin link never shows.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ui && npx vitest run src/auth/LoginPage.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Type-check**

Run: `cd ui && npx tsc -b`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add ui/src/auth/LoginPage.tsx ui/src/auth/LoginPage.test.tsx
git commit -m "UI: login page offers SSO with an admin sign-in link, or both when password login is open

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `/login/callback`: finish the code exchange and register

**Files:**
- Create: `ui/src/auth/completeSignIn.ts`
- Create: `ui/src/auth/completeSignIn.test.ts`
- Create: `ui/src/auth/LoginCallbackPage.tsx`
- Create: `ui/src/auth/LoginCallbackPage.test.tsx`
- Modify: `ui/src/App.tsx:28-30`

**Interfaces:**
- Consumes: `OidcSession`, `SessionUser` (Task 1); `register`, `ApiError`, `signOutLocally` (Task 2).
- Produces:
  - `completeSignIn(deps: { session: OidcSession; register: (idToken: string) => Promise<unknown> }): Promise<{ next: string }>`; throws `ApiError` from register unchanged, throws `Error("sign-in did not return an ID token")` when the IdP omitted it.
  - `CallbackStatus({ error })`: presentational.
  - `LoginCallbackPage()`: effect wrapper; on success navigates to `next` with `replace`.
  - Route `/login/callback` registered outside `AuthGate`.

- [ ] **Step 1: Write the failing tests**

`ui/src/auth/completeSignIn.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { completeSignIn } from "./completeSignIn";
import type { OidcSession } from "./oidc";

function sessionReturning(
  user: { access_token: string; id_token?: string; state?: unknown } | undefined,
): OidcSession & { removed: boolean } {
  const s = {
    removed: false,
    getUser: async () => null,
    signinRedirect: async () => {},
    signinSilent: async () => null,
    signinCallback: async () => user,
    removeUser: async () => {
      s.removed = true;
    },
  };
  return s;
}

describe("completeSignIn", () => {
  it("registers with the ID token and returns the next path from state", async () => {
    const register = vi.fn(async () => ({ username: "akshat" }));
    const result = await completeSignIn({
      session: sessionReturning({
        access_token: "idp",
        id_token: "id",
        state: "/datasets",
      }),
      register,
    });
    expect(register).toHaveBeenCalledWith("id");
    expect(result).toEqual({ next: "/datasets" });
  });

  it("falls back to / when the state is not a path", async () => {
    const result = await completeSignIn({
      session: sessionReturning({ access_token: "idp", id_token: "id" }),
      register: async () => ({}),
    });
    expect(result.next).toBe("/");
    const looped = await completeSignIn({
      session: sessionReturning({
        access_token: "idp",
        id_token: "id",
        state: "/login?next=%2F",
      }),
      register: async () => ({}),
    });
    expect(looped.next).toBe("/");
  });

  it("surfaces a 409 from register and signs the person out locally", async () => {
    const s = sessionReturning({ access_token: "idp", id_token: "id" });
    await expect(
      completeSignIn({
        session: s,
        register: async () => {
          throw new ApiError(
            409,
            "username akshat is taken by a user or workspace; ask an admin to bind subject 00u1 to an account",
          );
        },
      }),
    ).rejects.toMatchObject({ status: 409 });
    expect(s.removed).toBe(true);
  });

  it("fails when the IdP returned no ID token", async () => {
    const s = sessionReturning({ access_token: "idp" });
    await expect(
      completeSignIn({ session: s, register: async () => ({}) }),
    ).rejects.toThrow("sign-in did not return an ID token");
    expect(s.removed).toBe(true);
  });
});
```

`ui/src/auth/LoginCallbackPage.test.tsx`:

```tsx
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { CallbackStatus } from "./LoginCallbackPage";

describe("CallbackStatus", () => {
  it("says it is completing the sign-in while no error is known", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <CallbackStatus error={null} />
      </MemoryRouter>,
    );
    expect(html).toContain("Completing sign-in…");
    expect(html).not.toContain("Back to sign-in");
  });

  it("shows the server's message and a way back on error", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <CallbackStatus error="user is archived" />
      </MemoryRouter>,
    );
    expect(html).toContain("user is archived");
    expect(html).toContain('href="/login"');
    expect(html).toContain("Back to sign-in");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ui && npx vitest run src/auth/completeSignIn.test.ts src/auth/LoginCallbackPage.test.tsx`
Expected: FAIL with unresolved imports for both new modules.

- [ ] **Step 3: Write `completeSignIn.ts`**

```ts
import type { OidcSession } from "./oidc";

export function nextFromState(state: unknown): string {
  if (typeof state !== "string" || !state.startsWith("/")) return "/";
  if (state.startsWith("/login")) return "/";
  return state;
}

export async function completeSignIn(deps: {
  session: OidcSession;
  register: (idToken: string) => Promise<unknown>;
}): Promise<{ next: string }> {
  const user = await deps.session.signinCallback();
  if (user === undefined || user.id_token === undefined) {
    await deps.session.removeUser();
    throw new Error("sign-in did not return an ID token");
  }
  try {
    await deps.register(user.id_token);
  } catch (err) {
    await deps.session.removeUser();
    throw err;
  }
  return { next: nextFromState(user.state) };
}
```

- [ ] **Step 4: Write `LoginCallbackPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { register } from "../lib/api";
import { completeSignIn } from "./completeSignIn";
import { session } from "./oidc";

export function CallbackStatus({ error }: { error: string | null }) {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-canvas">
      <div className="card w-84 p-6">
        <h1 className="page-title">Evalkit</h1>
        {error === null ? (
          <p className="mt-1 text-meta text-ink-3">Completing sign-in…</p>
        ) : (
          <>
            <p className="mt-1 mb-4 text-meta text-err-text">{error}</p>
            <Link className="btn w-full justify-center" to="/login">
              Back to sign-in
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

export function LoginCallbackPage() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const s = await session();
      if (s === null) throw new Error("single sign-on is not configured");
      const { next } = await completeSignIn({ session: s, register });
      if (!cancelled) navigate(next, { replace: true });
    })().catch((err) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  return <CallbackStatus error={error} />;
}
```

- [ ] **Step 5: Add the route**

In `ui/src/App.tsx`, import the page and add the route next to `/login`, outside `AuthGate`:

```tsx
import { LoginCallbackPage } from "./auth/LoginCallbackPage";
// ...
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/login/callback" element={<LoginCallbackPage />} />
      <Route element={<AuthGate />}>
```

The server's SPA fallback in `src/evalkit/api/app.py` already serves `index.html` for any non-API, non-asset path, and the Vite dev server does the same, so a redirect to `/login/callback?code=...&state=...` reaches the app without a server change.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd ui && npx vitest run src/auth/completeSignIn.test.ts src/auth/LoginCallbackPage.test.tsx`
Expected: PASS, 6 tests.

- [ ] **Step 7: Type-check**

Run: `cd ui && npx tsc -b`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add ui/src/auth/completeSignIn.ts ui/src/auth/completeSignIn.test.ts ui/src/auth/LoginCallbackPage.tsx ui/src/auth/LoginCallbackPage.test.tsx ui/src/App.tsx
git commit -m "UI: /login/callback finishes the code exchange, registers, and navigates to next

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Sign out and the account chip

**Files:**
- Modify: `ui/src/shell/Shell.tsx:3` (import) and `:166-171, 186-191` (account chip and sign-out button)
- Create: `ui/src/shell/Shell.test.tsx`

**Interfaces:**
- Consumes: `logout()` (Task 2), `Me.email` (Task 2).
- Produces: `AccountChip({ me })` exported from `Shell.tsx`.

- [ ] **Step 1: Write the failing test**

`ui/src/shell/Shell.test.tsx`:

```tsx
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Me } from "../lib/types";
import { AccountChip } from "./Shell";

const base: Me = {
  username: "akshat",
  is_admin: false,
  email: null,
  kind: "human",
  personal_workspace: "akshat",
  roles: {},
};

describe("AccountChip", () => {
  it("shows only the username for a password account", () => {
    const html = renderToStaticMarkup(<AccountChip me={base} />);
    expect(html).toContain("akshat");
    expect(html).not.toContain("@example.com");
  });

  it("shows the email under the username for an SSO account", () => {
    const html = renderToStaticMarkup(
      <AccountChip me={{ ...base, email: "akshat@example.com" }} />,
    );
    expect(html).toContain("akshat");
    expect(html).toContain("akshat@example.com");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd ui && npx vitest run src/shell/Shell.test.tsx`
Expected: FAIL with `"AccountChip" is not exported`.

- [ ] **Step 3: Change `Shell.tsx`**

Add the component above `export function Shell()`:

```tsx
export function AccountChip({ me }: { me: Me }) {
  return (
    <span className="chip muted flex flex-col items-end leading-tight" title="signed in as">
      <span>{me.username}</span>
      {me.email !== null && (
        <span className="text-micro text-ink-4">{me.email}</span>
      )}
    </span>
  );
}
```

Add `import type { Me } from "../lib/types";` next to the existing `WorkspaceInfo` type import (it can share the line: `import type { Me, WorkspaceInfo } from "../lib/types";`).

In the header, replace

```tsx
          <span className="chip muted" title="signed in as">
            {me.username}
          </span>
```

with `<AccountChip me={me} />`.

The sign-out button already calls `logout().finally(() => navigate("/login"))`; `logout()` from Task 2 now decides between the password path and `removeUser()`, so the button needs no change.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd ui && npx vitest run src/shell/Shell.test.tsx`
Expected: PASS, 2 tests.

- [ ] **Step 5: Run the whole UI suite and the build**

Run: `cd ui && npm test && npm run build`
Expected: all tests pass; `tsc -b` and `vite build` succeed.

- [ ] **Step 6: Commit**

```bash
git add ui/src/shell/Shell.tsx ui/src/shell/Shell.test.tsx
git commit -m "UI: account chip shows the SSO email; sign out clears whichever credential is in use

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Browser pass, screenshots, changelog and docs

**Files:**
- Modify: `CHANGELOG.md` (under `## Unreleased`)
- Modify: `docs/user/login.md` (the SSO section Plan 2 added)
- Not committed: `config.yaml`, screenshots, `body.md`

**Interfaces:**
- Consumes: the fake IdP that Plan 2 added to `tests/e2e/services.py` on port 19100 (discovery, JWKS, an authorize endpoint that redirects straight back with a code, a token endpoint that accepts any `redirect_uri` and returns an ID token with `email` and `preferred_username`).

- [ ] **Step 1: Start the services**

In three terminals, from the repo root:

```bash
# 1. fake harness, evaluator, judge and IdP
uv run python tests/e2e/services.py

# 2. the server, against a throwaway local Postgres, with SSO pointed at the fake IdP
cat > config.yaml <<'EOF'
server:
  base_url: http://127.0.0.1:5180
db:
  url: postgresql+asyncpg://evalkit:evalkit@localhost:5432/evalkit
blob:
  url: file:///tmp/evalkit-blobs
encryption_key: 0000000000000000000000000000000000000000000000000000000000000000
auth:
  password_login: admin
  oidc:
    issuer: http://127.0.0.1:19100
    client_id: evalkit-ui
    audience: http://127.0.0.1:5180
    name: Fake IdP
EOF
uv run evalkit db upgrade --config config.yaml
uv run evalkit blobs relocate --config config.yaml
EVALKIT_ADMIN_PASSWORD=admin uv run evalkit serve --config config.yaml

# 3. the UI
cd ui && npm run dev
```

Check the server's first log lines show discovery succeeded (no `identity provider unreachable`). Check `curl -s http://127.0.0.1:5180/api/auth/providers` returns `"password":"admin"` and an `oidc` object naming `Fake IdP`.

- [ ] **Step 2: Click through and screenshot**

Open `http://127.0.0.1:5180/login` and capture each state to `./shots/`:

1. `login-admin-mode.png`: the "Continue with Fake IdP" button and the "Admin sign-in" link, no form.
2. `login-admin-form.png`: after clicking "Admin sign-in", the username and password form under the button.
3. `callback.png`: click "Continue with Fake IdP"; the fake IdP redirects back; capture the home page with the account chip showing the username and the fake email. Confirm the URL went through `/login/callback` (browser history).
4. `account-chip.png`: the header with username and email.
5. `callback-409.png`: sign out, then make the fake IdP return a `preferred_username` that matches an existing workspace name (create a shared workspace named like the fake user with `uv run evalkit workspaces create <name>` as admin first), sign in again, capture the 409 message and the "Back to sign-in" link.
6. `signout.png`: after "Sign out", the login page again; confirm `localStorage` has no `oidc.user:` key (devtools, Application tab).
7. Switch `password_login: all` in `config.yaml`, restart the server, capture `login-all-mode.png`: button above the open form, no admin link.
8. Admin password login still works in `admin` mode: sign in as `admin` / `admin` through the form, capture `admin-signed-in.png`.

Also confirm, without a screenshot: refresh the page after an SSO sign-in and the session survives (local storage); a page left open past the fake IdP's access token lifetime keeps working (the fake IdP issues short tokens; watch the network tab for a `POST /token` with `grant_type=refresh_token`).

- [ ] **Step 3: Changelog**

Add under `## Unreleased` in `CHANGELOG.md`:

```markdown
- The web UI signs in through the configured identity provider: the login
  page shows "Continue with <name>" when the server has `auth.oidc`, with an
  "Admin sign-in" link to the password form (or the form open under the
  button when `auth.password_login` is `all`). The new `/login/callback`
  route completes the sign-in. The account chip shows the SSO email under
  the username, and sign out clears the SSO session locally.
```

- [ ] **Step 4: Docs**

In `docs/user/login.md`, in the SSO section Plan 2 added, replace the UI sentence (Plan 2 wrote a one-line placeholder saying the web UI offers the same sign-in) with:

```markdown
In the web UI, the login page shows **Continue with <provider>** when single
sign-on is configured. The first sign-in creates your evalkit user from the
identity provider's account; later sign-ins find it. An admin can still use
the username and password form behind **Admin sign-in** (or under the button
when the server allows password login for everyone). Sign out clears the
session in this browser only; your identity provider session stays.
```

Run `uv run --group docs mkdocs build --strict` from the repo root and expect no warnings.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md docs/user/login.md
git commit -m "UI SSO: changelog entry and the login page docs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

- [ ] **Step 6: Open the draft PR with screenshots**

Write `body.md` (not committed):

```markdown
## TL;DR

The web UI signs in through the configured OIDC identity provider. Plan 3 of the SSO spec (`docs/superpowers/specs/2026-11-03-oidc-sso-and-tenancy-design.md`, section Web UI).

## How

`oidc-client-ts` runs the authorization code + PKCE flow and keeps the tokens in local storage. `lib/api.ts` hands out the admin password token or the IdP access token per request, refreshing within a minute of expiry. `/login` shows the SSO button with an admin sign-in link; `/login/callback` finishes the exchange and calls `POST /api/auth/oidc/register` once. A 401 after sign-in clears both credentials and returns to `/login`.

## Testing

`cd ui && npm test` (oidc module, bearer precedence and refresh, login page states, callback flow, account chip) and `npm run build`. Browser pass against the fake IdP in `tests/e2e/services.py`, below.

## Screenshots

![Login, admin-only password mode](./shots/login-admin-mode.png)
![Admin sign-in form revealed](./shots/login-admin-form.png)
![Signed in through SSO](./shots/callback.png)
![Account chip with email](./shots/account-chip.png)
![Registration refused: username taken](./shots/callback-409.png)
![Signed out](./shots/signout.png)
![Login, password for everyone](./shots/login-all-mode.png)
![Admin password sign-in](./shots/admin-signed-in.png)

## Checklist

- [x] The pull request targets `develop`
- [x] Tests, `ruff` and `mypy` pass locally
- [x] `CHANGELOG.md` has an entry under `## Unreleased`
- [x] Docs updated (`docs/user/login.md`)
- [x] Screenshots added

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Then:

```bash
git push -u origin <branch>
gh pr create --draft --base develop --title "Web UI signs in through the configured OIDC provider" --body-file body.md
gh pr edit <number> --body-file body.md \
  --attach ./shots/login-admin-mode.png --attach ./shots/login-admin-form.png \
  --attach ./shots/callback.png --attach ./shots/account-chip.png \
  --attach ./shots/callback-409.png --attach ./shots/signout.png \
  --attach ./shots/login-all-mode.png --attach ./shots/admin-signed-in.png
```

`gh pr edit --attach` needs gh ≥ 2.99; it uploads each file and rewrites the `./shots/...` links. Delete `config.yaml`, `shots/` and `body.md` afterwards; none of them are staged.

---

## Self-review

**Spec coverage (section "Web UI"):**

| Spec item | Task |
| --- | --- |
| `oidc-client-ts`, `UserManager` from providers, local storage store, no automatic renew | 1 |
| `/login` three states, `next` carried in state | 3 |
| `/login/callback`: `signinCallback`, register, navigate, 409 and archived shown, person left signed out | 4 |
| SPA fallback needs no server change | 4, step 5 |
| Logout: `removeUser()` for SSO, `POST /auth/logout` for password; IdP session not ended | 2, 5 |
| Account menu shows email | 5 |
| `bearer()` prefers password token, refreshes within 60 s | 2 |
| 401 after sign-in clears both and goes to `/login?next=` | 2 |
| `not registered` handled on the callback page, never in `apiFetch` | 4 (register runs before any other call) |
| Download tickets unchanged | nothing to do; `apiFetch` still carries the bearer |
| Tests listed in the spec's UI section | 1–5 |
| Browser pass with screenshots; changelog; docs | 6 |

**Placeholder scan:** none. Every step has its code or command.

**Type consistency:** `OidcSession`, `SessionUser`, `Providers`, `OidcProviders` are defined in Task 1 and imported by name in Tasks 2–4; `register(idToken: string): Promise<Me>` in Task 2 matches `completeSignIn`'s `register: (idToken: string) => Promise<unknown>` in Task 4; `Me.email: string | null` in Task 2 matches `AccountChip` in Task 5.
