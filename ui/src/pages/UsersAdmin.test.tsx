// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { setSessionForTest } from "../auth/oidc";
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
});

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

test("with password login on, an admin adds a user with a password", async () => {
  stubServer(() => undefined);
  renderUsersPage();
  expect(
    await screen.findByRole("button", { name: "Add user" }),
  ).toBeInTheDocument();
  expect(document.querySelector("input[type=password]")).not.toBeNull();
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
