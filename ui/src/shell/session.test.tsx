// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient, useApi } from "../lib/query";
import { AuthGate } from "./capabilities";

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

// Like the users page: rows and the error banner render side by side.
function UserList() {
  const users = useApi<{ username: string }[]>("/users");
  return (
    <>
      {users.error && <p>{users.error.message}</p>}
      <ul>
        {(users.data ?? []).map((u) => (
          <li key={u.username}>{u.username}</li>
        ))}
      </ul>
    </>
  );
}

test("signing in never shows the previous user's cached pages", async () => {
  queryClient.setQueryData(["/users"], [{ username: "alice" }]);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/auth/me"))
        return Response.json({
          username: "bob",
          is_admin: false,
          email: null,
          kind: "human",
          personal_workspace: "bob",
          roles: {},
        });
      if (url.endsWith("/api/meta"))
        return Response.json({ agents: [], agent_repos: {} });
      return Response.json({ detail: "admin only" }, { status: 403 });
    }),
  );

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/users"]}>
        <Routes>
          <Route element={<AuthGate />}>
            <Route path="/users" element={<UserList />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

  await screen.findByText("admin only");
  expect(screen.queryByText("alice")).toBeNull();
});
