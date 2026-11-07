import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Me } from "../lib/types";
import { MeContext, RequireMe } from "./capabilities";
import { ShellOrBare, WorkspaceOrPublic } from "./layout";

const me: Me = {
  username: "akshat",
  is_admin: false,
  email: null,
  kind: "human",
  personal_workspace: "akshat",
  roles: {},
};

function render(signedIn: Me | null, path: string): string {
  return renderToStaticMarkup(
    <MeContext.Provider value={signedIn}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<WorkspaceOrPublic />}>
            <Route element={<ShellOrBare />}>
              <Route path="/docs/:tab" element={<div>docs body</div>} />
              <Route element={<RequireMe />}>
                <Route path="/datasets" element={<div>datasets body</div>} />
              </Route>
            </Route>
          </Route>
        </Routes>
      </MemoryRouter>
    </MeContext.Provider>,
  );
}

describe("signed-out layout", () => {
  it("shows docs under the public navbar with a sign-in link back to the page", () => {
    const html = render(null, "/docs/install");
    expect(html).toContain("docs body");
    expect(html).toContain('href="/login?next=%2Fdocs%2Finstall"');
  });

  it("does not render private pages", () => {
    expect(render(null, "/datasets")).not.toContain("datasets body");
  });
});

describe("signed-in layout", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("waits for the workspace gate instead of the public navbar", () => {
    vi.stubGlobal("localStorage", { getItem: () => null });
    const html = render(me, "/docs/install");
    expect(html).not.toContain("Sign in");
    expect(html).toContain("Loading");
  });
});
