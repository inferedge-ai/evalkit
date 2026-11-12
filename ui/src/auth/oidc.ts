import { useQuery } from "@tanstack/react-query";
import {
  UserManager,
  WebStorageStateStore,
  type RefreshState,
  type SigninSilentArgs,
  type User,
  type UserManagerSettings,
} from "oidc-client-ts";

const API = import.meta.env.VITE_API_BASE_URL ?? "";

// Discovery and the code exchange run while sign-in or the refresh lock waits.
const IDP_REQUEST_TIMEOUT_SECONDS = 10;

export type OidcProviders = {
  name: string;
  issuer: string;
  client_id: string;
  scopes: string[];
  authorize_params: Record<string, string>;
  ui_resources: string[];
  authorization_endpoint: string;
  token_endpoint: string;
  device_authorization_endpoint: string | null;
};

export type Providers = {
  password: "all" | "admin" | "none";
  oidc: OidcProviders | null;
};

export interface SessionUser {
  access_token: string;
  id_token?: string;
  refresh_token?: string;
  expires_in?: number;
  state?: unknown;
  profile?: { sub: string };
}

export type ApiToken = {
  access_token: string;
  expires_in?: number;
  sub: string;
};

export interface OidcSession {
  getUser(): Promise<SessionUser | null>;
  signinRedirect(args: {
    state: unknown;
    prompt?: string;
    redirectMethod?: "replace" | "assign";
  }): Promise<void>;
  signinCallback(): Promise<SessionUser | undefined>;
  signinSilent(): Promise<SessionUser | null>;
  removeUser(): Promise<void>;
  requestApiToken(resource: string): Promise<ApiToken | null>;
}

class EvalkitUserManager extends UserManager {
  // With ui_resources the sign-in names several APIs, so evalkit's own
  // refresh names evalkit's (userManagerSettings puts it in extraTokenParams).
  override signinSilent(args: SigninSilentArgs = {}): Promise<User | null> {
    return super.signinSilent({
      extraTokenParams: this.settings.extraTokenParams,
      silentRequestTimeoutInSeconds:
        this.settings.silentRequestTimeoutInSeconds,
      ...args,
    });
  }

  // Refreshes for another API without replacing the stored evalkit token, and
  // keeps the rotated refresh token: the provider revokes a reused one.
  async requestApiToken(resource: string): Promise<ApiToken | null> {
    const user = await this.getUser();
    if (user?.refresh_token === undefined) return null;
    // oidc-client-ts types RefreshState as exported but doesn't export it; this
    // is the object it builds from a User.
    const state: RefreshState = {
      refresh_token: user.refresh_token,
      id_token: user.id_token,
      session_state: user.session_state,
      scope: user.scope,
      profile: user.profile,
      data: user.state,
    };
    const response = await this._client.useRefreshToken({
      state,
      resource,
      timeoutInSeconds: this.settings.silentRequestTimeoutInSeconds,
    });
    // A sign-out in another tab while the request ran must stay signed out.
    const current = await this.getUser();
    if (current?.refresh_token !== user.refresh_token) return null;
    if (response.refresh_token) {
      current.refresh_token = response.refresh_token;
      await this.storeUser(current);
    }
    return {
      access_token: response.access_token,
      expires_in: response.expires_in,
      sub: user.profile.sub,
    };
  }
}

let providersPromise: Promise<Providers> | null = null;

export class ProvidersUnavailable extends Error {
  password: Providers["password"] | null;
  constructor(message: string, password: Providers["password"] | null) {
    super(message);
    this.password = password;
  }
}

export function loadProviders(): Promise<Providers> {
  if (providersPromise === null) {
    providersPromise = fetch(`${API}/api/auth/providers`).then(async (resp) => {
      const body = await resp
        .json()
        .catch(() => ({}) as Record<string, unknown>);
      if (!resp.ok) {
        providersPromise = null;
        const message = String(body.detail ?? "cannot load sign-in options");
        if (resp.status !== 503) throw new Error(message);
        throw new ProvidersUnavailable(
          message,
          (body.password as Providers["password"] | undefined) ?? null,
        );
      }
      return body as Providers;
    });
  }
  return providersPromise;
}

// undefined while loading, null when the server didn't say.
export function usePasswordLogin(): Providers["password"] | null | undefined {
  const providers = useQuery({
    queryKey: ["auth-providers"],
    queryFn: loadProviders,
  });
  if (providers.isPending) return undefined;
  if (providers.data) return providers.data.password;
  return providers.error instanceof ProvidersUnavailable
    ? providers.error.password
    : null;
}

export function resetProvidersForTest(): void {
  providersPromise = null;
}

export function userManagerSettings(
  oidc: OidcProviders,
  origin: string,
): UserManagerSettings {
  const store = typeof localStorage !== "undefined" ? localStorage : undefined;
  const { resource, ...authorizeParams } = oidc.authorize_params;
  return {
    authority: oidc.issuer,
    client_id: oidc.client_id,
    redirect_uri: `${origin}/login/callback`,
    scope: oidc.scopes.join(" "),
    response_type: "code",
    resource: resource ? [resource, ...oidc.ui_resources] : undefined,
    extraTokenParams:
      resource && oidc.ui_resources.length > 0 ? { resource } : undefined,
    extraQueryParams: authorizeParams,
    userStore:
      store === undefined ? undefined : new WebStorageStateStore({ store }),
    automaticSilentRenew: false,
    requestTimeoutInSeconds: IDP_REQUEST_TIMEOUT_SECONDS,
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
        : new EvalkitUserManager(
            userManagerSettings(providers.oidc, window.location.origin),
          );
  }
  return manager;
}

export function setSessionForTest(s: OidcSession | null | undefined): void {
  override = s;
  manager = undefined;
}
