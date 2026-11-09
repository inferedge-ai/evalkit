// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { Me } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { renderPage, stubServer } from "../testFixtures";
import { LlmProvidersPage } from "./LlmProvidersPage";

// The provider form imports a file outside ui/ that jsdom runs can't load.
vi.mock("./LlmProviderForm", () => ({ LlmProviderForm: () => null }));

const admin: Me = {
  username: "root",
  is_admin: true,
  email: null,
  kind: "human",
  personal_workspace: "root",
  roles: {},
};

const judge = (model: string) => ({
  provider: "openai",
  model,
  reasoning_effort: "high",
});

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

test("reopening the page shows the server's current default judge", async () => {
  queryClient.setQueryData(["/judge-default"], judge("old-model"));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/judge-default"))
        return Response.json(judge("new-model"));
      return Response.json([]);
    }),
  );

  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={admin}>
        <LlmProvidersPage />
      </MeContext.Provider>
    </QueryClientProvider>,
  );

  await waitFor(() =>
    expect(
      (screen.getByPlaceholderText("model") as HTMLInputElement).value,
    ).toBe("new-model"),
  );
});

test("without providers, an admin is told how to add one", async () => {
  stubServer((url) =>
    url.endsWith("/api/judge-default")
      ? Response.json({ detail: "no default judge" }, { status: 404 })
      : undefined,
  );
  renderPage(
    admin,
    "/settings/llm-providers",
    <Route path="/settings/llm-providers" element={<LlmProvidersPage />} />,
  );
  expect(await screen.findByText("No LLM providers")).toBeInTheDocument();
  expect(screen.getByText(/Add one with New provider/)).toBeInTheDocument();
  expect(screen.getByText("evalkit llm-providers create")).toBeInTheDocument();
});
