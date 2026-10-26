import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { NotFound } from "./NotFound";

describe("NotFound", () => {
  it("names the missing thing and links to its parent", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <NotFound
          what="dataset finance@dev"
          up={{ label: "All datasets", to: "/datasets" }}
        />
      </MemoryRouter>,
    );
    expect(html).toContain("dataset finance@dev");
    expect(html).toContain('href="/datasets"');
    expect(html).toContain("All datasets");
  });
});
