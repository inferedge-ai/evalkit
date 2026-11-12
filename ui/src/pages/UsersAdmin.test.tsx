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
  stubPasswordLogin,
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

function stubUsers(status: number, password: string) {
  stubPasswordLogin(status, password, (url) => {
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

test("with password login on, the page shows the username and password form", async () => {
  stubServer(() => undefined);
  renderUsersPage();
  expect(
    await screen.findByRole("button", { name: "Add user" }),
  ).toBeInTheDocument();
  expect(document.querySelector("input[type=password]")).not.toBeNull();
});

test("a refused row action shows its error", async () => {
  stubUsers(200, "admin");
  renderUsersPage();
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: "demote" }));
  expect(
    await screen.findByText("cannot remove the last admin"),
  ).toBeInTheDocument();
});

test("while the identity provider is unreachable, password login for admins keeps the form", async () => {
  stubUsers(503, "admin");
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
