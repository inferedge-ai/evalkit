// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { SidebarProvider } from "@/components/ui/sidebar";
import { queryClient } from "../lib/query";
import type { Me } from "../lib/types";
import { MeContext } from "../shell/capabilities";
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
        <SidebarProvider>
          <LlmProvidersPage />
        </SidebarProvider>
      </MeContext.Provider>
    </QueryClientProvider>,
  );

  await waitFor(() =>
    expect(
      (screen.getByPlaceholderText("model") as HTMLInputElement).value,
    ).toBe("new-model"),
  );
});
