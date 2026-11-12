// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { resetProvidersForTest, setSessionForTest } from "../auth/oidc";
import {
  closePage,
  failure,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { UsersAdminPage } from "./UsersAdmin";

afterEach(() => {
  closePage();
  setSessionForTest(undefined);
  resetProvidersForTest();
});

const ONLY_ADMIN = {
  id: 1,
  username: "ann",
  is_admin: true,
  archived: false,
  created_at: "2026-11-01T00:00:00Z",
};

function stubPasswordLogin(status: number, password: string) {
  setSessionForTest(null);
  stubServer((url) => {
    if (url.endsWith("/api/auth/providers"))
      return Response.json(
        status === 200
          ? { password, oidc: { name: "Okta" } }
          : { detail: "identity provider unreachable", password },
        { status },
      );
    if (url.endsWith("/api/users")) return Response.json([ONLY_ADMIN]);
    if (url.endsWith("/api/users/ann/demote"))
      return Response.json(
        { detail: "cannot remove the last admin" },
        { status: 409 },
      );
    return undefined;
  });
}

function renderUsersPage() {
  renderPage(
    { ...member({}), is_admin: true },
    "/settings/users",
    <Route path="/settings/users" element={<UsersAdminPage />} />,
  );
}

test("with password login off, the page says how people join instead of offering a password form", async () => {
  setSessionForTest(null);
  stubServer((url) =>
    url.endsWith("/api/auth/providers")
      ? Response.json({ password: "none", oidc: { name: "Okta" } })
      : undefined,
  );
  renderUsersPage();
  expect(
    await screen.findByText(/People join by signing in with Okta/),
  ).toBeInTheDocument();
  expect(document.querySelector("input[type=password]")).toBeNull();
  expect(screen.queryByRole("button", { name: "Add user" })).toBeNull();
});

test("with password login on, the page shows the username and password form", async () => {
  stubServer(() => undefined);
  renderUsersPage();
  expect(
    await screen.findByRole("button", { name: "Add user" }),
  ).toBeInTheDocument();
  expect(document.querySelector("input[type=password]")).not.toBeNull();
});

test("with password login off, a refused row action still shows its error", async () => {
  stubPasswordLogin(200, "none");
  renderUsersPage();
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: "demote" }));
  expect(
    await screen.findByText("cannot remove the last admin"),
  ).toBeInTheDocument();
});

test("while the identity provider is unreachable, password login off still hides the form", async () => {
  stubPasswordLogin(503, "none");
  renderUsersPage();
  expect(
    await screen.findByText(/People join by signing in with/),
  ).toBeInTheDocument();
  expect(document.querySelector("input[type=password]")).toBeNull();
});

test("while the identity provider is unreachable, password login for admins keeps the form", async () => {
  stubPasswordLogin(503, "admin");
  renderUsersPage();
  expect(
    await screen.findByRole("button", { name: "Add user" }),
  ).toBeInTheDocument();
});

test("a user list that fails to load shows the error instead of placeholder rows", async () => {
  stubServer((url) =>
    url.endsWith("/api/users") ? failure("database unavailable") : undefined,
  );
  renderPage(
    { ...member({}), is_admin: true },
    "/settings/users",
    <Route path="/settings/users" element={<UsersAdminPage />} />,
  );
  expect(await screen.findByText(/database unavailable/)).toBeInTheDocument();
  expect(document.querySelector("[data-slot=skeleton]")).toBeNull();
});
