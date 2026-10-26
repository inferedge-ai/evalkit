import { describe, expect, it } from "vitest";
import { httpUrl } from "./safeUrl";

describe("httpUrl", () => {
  it("keeps http and https urls", () => {
    expect(httpUrl("https://github.com/a/b")).toBe("https://github.com/a/b");
    expect(httpUrl("HTTP://git/x")).toBe("HTTP://git/x");
    expect(httpUrl(" https://github.com/a/b ")).toBe("https://github.com/a/b");
  });

  it("drops other schemes and empty values", () => {
    expect(httpUrl("javascript:alert(1)")).toBeNull();
    expect(httpUrl(" JavaScript:alert(1)")).toBeNull();
    expect(httpUrl("data:text/html,x")).toBeNull();
    expect(httpUrl("//evil.example")).toBeNull();
    expect(httpUrl("")).toBeNull();
    expect(httpUrl(null)).toBeNull();
    expect(httpUrl(undefined)).toBeNull();
  });
});
