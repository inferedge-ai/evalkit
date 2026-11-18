// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { DatasetAbout } from "./DatasetAbout";

function show(metadata: Parameters<typeof DatasetAbout>[0]["metadata"]) {
  return render(
    <MemoryRouter>
      <DatasetAbout metadata={metadata} />
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("DatasetAbout", () => {
  it("shows the description, attribution, licence, links and other keys", () => {
    show({
      description: "Multi-hop questions.",
      attribution: "By someone.",
      license: { name: "MIT", url: "https://opensource.org/license/mit" },
      links: [
        { label: "Paper", url: "https://example.com/paper" },
        { label: "Catalog entry", url: "/catalog/tiny" },
      ],
      team: "research",
    });

    expect(screen.getByText("Multi-hop questions.")).toBeTruthy();
    expect(screen.getByText("By someone.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "MIT" }).getAttribute("href")).toBe(
      "https://opensource.org/license/mit",
    );
    const paper = screen.getByRole("link", { name: "Paper" });
    expect(paper.getAttribute("target")).toBe("_blank");
    expect(
      screen.getByRole("link", { name: "Catalog entry" }).getAttribute("href"),
    ).toBe("/catalog/tiny");
    expect(screen.getByText("research")).toBeTruthy();
  });

  it("does not turn a script or protocol-relative URL into a link", () => {
    show({
      links: [
        { label: "Script", url: "javascript:alert(1)" },
        { label: "Elsewhere", url: "//evil.example" },
      ],
    });

    expect(screen.queryAllByRole("link")).toEqual([]);
    expect(screen.getByText("Script")).toBeTruthy();
  });

  it("renders nothing for a dataset without metadata", () => {
    const { container } = show({});

    expect(container.innerHTML).toBe("");
  });
});
