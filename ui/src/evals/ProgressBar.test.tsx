import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ProgressBar } from "./ProgressBar";

describe("ProgressBar", () => {
  it("labels ran and evaluated over total and sizes both segments", () => {
    const html = renderToStaticMarkup(
      <ProgressBar progress={{ total: 40, ran: 12, evaluated: 8 }} />,
    );
    expect(html).toContain("12/40 ran · 8/40 evaluated");
    expect(html).toContain("width:30%");
    expect(html).toContain("width:20%");
  });

  it("copes with an experiment that pinned no cases", () => {
    const html = renderToStaticMarkup(
      <ProgressBar progress={{ total: 0, ran: 0, evaluated: 0 }} />,
    );
    expect(html).toContain("0/0 ran · 0/0 evaluated");
  });
});
