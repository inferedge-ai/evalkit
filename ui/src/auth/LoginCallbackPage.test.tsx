import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { CallbackStatus } from "./LoginCallbackPage";

describe("CallbackStatus", () => {
  it("says it is completing the sign-in while no error is known", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <CallbackStatus error={null} />
      </MemoryRouter>,
    );
    expect(html).toContain("Completing sign-in…");
    expect(html).not.toContain("Try again");
  });

  it("shows the server's message and a way back on error", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <CallbackStatus error="user is archived" />
      </MemoryRouter>,
    );
    expect(html).toContain("user is archived");
    expect(html).toContain('href="/login"');
    expect(html).toContain("Try again");
  });
});
