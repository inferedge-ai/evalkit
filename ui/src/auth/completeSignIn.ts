import { safeNext } from "./next";
import type { OidcSession } from "./oidc";

export async function completeSignIn(deps: {
  session: OidcSession;
  register: (idToken: string) => Promise<unknown>;
}): Promise<{ next: string }> {
  const user = await deps.session.signinCallback();
  if (user === undefined || user.id_token === undefined) {
    await deps.session.removeUser();
    throw new Error("sign-in did not return an ID token");
  }
  await deps.register(user.id_token).catch(async (err: unknown) => {
    await deps.session.removeUser();
    throw err;
  });
  return {
    next: safeNext(typeof user.state === "string" ? user.state : null),
  };
}
