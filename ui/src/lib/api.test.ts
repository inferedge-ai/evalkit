import { describe, expect, it } from "vitest";
import {
  ApiError,
  NotFoundError,
  detailText,
  isMissing,
  noteServerDate,
  serverNow,
} from "./api";

describe("api errors", () => {
  it("formats validation details instead of [object Object]", () => {
    expect(
      detailText([
        { loc: ["path", "id"], msg: "Input should be a valid integer" },
        { msg: "second" },
      ]),
    ).toBe("Input should be a valid integer; second");
    expect(detailText("plain")).toBe("plain");
    expect(detailText({ a: 1 })).toBe('{"a":1}');
  });

  it("treats 400, 404 and 422 as a dead link", () => {
    expect(isMissing(new NotFoundError("x"))).toBe(true);
    expect(isMissing(new ApiError(422, "x"))).toBe(true);
    expect(isMissing(new ApiError(400, "x"))).toBe(true);
    expect(isMissing(new ApiError(500, "x"))).toBe(false);
    expect(isMissing(new Error("x"))).toBe(false);
  });
});

describe("serverNow", () => {
  it("follows the server's clock from its Date header", () => {
    noteServerDate(new Date(Date.now() + 60_000).toUTCString());
    expect(serverNow() - Date.now()).toBeGreaterThan(58_000);
    noteServerDate("not a date");
    expect(serverNow() - Date.now()).toBeGreaterThan(58_000);
    noteServerDate(new Date().toUTCString());
    expect(Math.abs(serverNow() - Date.now())).toBeLessThan(1_500);
  });
});
