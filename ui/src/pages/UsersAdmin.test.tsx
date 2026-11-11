// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import {
  closePage,
  failure,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { UsersAdminPage } from "./UsersAdmin";

afterEach(closePage);

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
