import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { completeSignIn } from "./completeSignIn";
import type { OidcSession } from "./oidc";

function sessionReturning(
  user:
    { access_token: string; id_token?: string; state?: unknown } | undefined,
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

  it("ignores a state with control characters", async () => {
    const result = await completeSignIn({
      session: sessionReturning({
        access_token: "idp",
        id_token: "id",
        state: "/\t/evil.example",
      }),
      register: async () => ({}),
    });
    expect(result.next).toBe("/");
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
