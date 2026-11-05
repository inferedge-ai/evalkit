import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Me } from "../lib/types";
import { AccountChip } from "./Shell";

const base: Me = {
  username: "akshat",
  is_admin: false,
  email: null,
  kind: "human",
  personal_workspace: "akshat",
  roles: {},
};

describe("AccountChip", () => {
  it("shows only the username for a password account", () => {
    const html = renderToStaticMarkup(<AccountChip me={base} />);
    expect(html).toContain("akshat");
    expect(html).not.toContain("@example.com");
  });

  it("shows the email under the username for an SSO account", () => {
    const html = renderToStaticMarkup(
      <AccountChip me={{ ...base, email: "akshat@example.com" }} />,
    );
    expect(html).toContain("akshat");
    expect(html).toContain("akshat@example.com");
  });
});
