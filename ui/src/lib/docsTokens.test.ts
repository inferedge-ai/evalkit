import { describe, expect, it } from "vitest";
import siteTokens from "../../../docs/site/tokens.py?raw";
import tailwindTheme from "tailwindcss/theme.css?raw";

describe("the docs site's copy of Tailwind's colours", () => {
  const copied = [...siteTokens.matchAll(/"(--color-[\w-]+)": "([^"]+)"/g)];

  it("lists the colours", () => {
    expect(copied.length).toBeGreaterThan(10);
  });

  it.each(copied.map(([, name, value]) => [name, value]))(
    "%s matches the installed Tailwind",
    (name, value) => {
      expect(tailwindTheme).toContain(`${name}: ${value};`);
    },
  );
});
