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
  refresh_token?: string;
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
  const store = typeof localStorage !== "undefined" ? localStorage : undefined;
  return {
    authority: oidc.issuer,
    client_id: oidc.client_id,
    redirect_uri: `${origin}/login/callback`,
    scope: oidc.scopes.join(" "),
    response_type: "code",
    extraQueryParams: oidc.authorize_params,
    userStore:
      store === undefined ? undefined : new WebStorageStateStore({ store }),
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
