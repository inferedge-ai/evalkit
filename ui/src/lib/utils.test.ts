import { expect, test } from "vitest";
import { cn } from "./utils";

test("cn keeps an evalkit font size next to a text colour", () => {
  expect(cn("text-meta text-ink-2")).toBe("text-meta text-ink-2");
});
