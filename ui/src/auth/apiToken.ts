import { ErrorResponse } from "oidc-client-ts";
import { withRefreshLock } from "../lib/api";
import { loadProviders, session } from "./oidc";

const EXPIRY_MARGIN_MS = 60_000;

// The provider's answers that mean this session can't get the token; signing
// in again fixes them. Any other error is thrown to the page.
const SESSION_REFUSALS = new Set([
  "invalid_target",
  "invalid_grant",
  "invalid_scope",
]);

type CachedToken = { token: string; sub: string; expiresAt: number };

const cached = new Map<string, CachedToken>();

function cachedToken(resource: string, sub: string): string | null {
  const hit = cached.get(resource);
  if (!hit || hit.sub !== sub) return null;
  return hit.expiresAt - Date.now() > EXPIRY_MARGIN_MS ? hit.token : null;
}

export async function getApiToken(resource: string): Promise<string | null> {
  const providers = await loadProviders();
  if (!providers.oidc?.ui_resources.includes(resource))
    throw new Error(`${resource} is not one of auth.oidc.ui_resources`);
  const s = await session();
  if (!s) return null;
  const signedIn = (await s.getUser())?.profile?.sub;
  const ready = signedIn ? cachedToken(resource, signedIn) : null;
  if (ready) return ready;
  return withRefreshLock(async () => {
    const sub = (await s.getUser())?.profile?.sub;
    if (!sub) return null;
    const hit = cachedToken(resource, sub);
    if (hit) return hit;
    try {
      const got = await s.requestApiToken(resource);
      if (got === null) return null;
      const expiresIn = (got.expires_in ?? 0) * 1000;
      cached.set(resource, {
        token: got.access_token,
        sub: got.sub,
        expiresAt: Date.now() + expiresIn,
      });
      return got.access_token;
    } catch (e: unknown) {
      if (e instanceof ErrorResponse && SESSION_REFUSALS.has(e.error ?? ""))
        return null;
      throw e;
    }
  });
}

export function resetApiTokensForTest(): void {
  cached.clear();
}
