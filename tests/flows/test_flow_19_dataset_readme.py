from tests.flows.helpers_01_06 import api, cases_yaml, cli, live, stub_judges_module

__all__ = ["cli", "live", "stub_judges_module"]

README = """---
description: Questions about quarterly filings.
license:
  name: MIT
  url: https://opensource.org/license/mit
attribution: The finance team.
links:
  - label: Wiki
    url: https://example.com/wiki
---

# Filings

How these cases were written.
"""


def test_flow_19_a_readme_in_the_dataset_folder_is_synced_shown_and_cleared(cli):
    cli.as_user("alice")
    root = cli.suite("rd19", {"a.yaml": cases_yaml("rd19-1"), "README.md": README})

    assert "0 error(s)" in cli.ok("lint", "suites/rd19")
    assert "readme=updated" in cli.ok("sync", "rd19")
    assert "readme=updated" not in cli.ok("sync", "rd19")

    with api(cli.live, "alice") as alice:
        dataset = alice.get("/api/datasets/rd19@alice").json()
        assert dataset["readme"] == "# Filings\n\nHow these cases were written.\n"
        assert dataset["metadata"]["description"] == "Questions about quarterly filings."
        assert dataset["metadata"]["license"]["name"] == "MIT"
        assert dataset["last_sync"]["detail"].get("readme_changed") is None
        [listed] = [d for d in alice.get("/api/datasets").json() if d["ref"] == "rd19@alice"]
        assert listed["metadata"]["links"] == [{"label": "Wiki", "url": "https://example.com/wiki"}]
        assert "readme" not in listed

    assert "Questions about quarterly filings." in cli.ok("datasets", "list")
    shown = cli.ok("datasets", "show", "rd19")
    assert "description: Questions about quarterly filings." in shown
    assert "license: MIT https://opensource.org/license/mit" in shown
    assert "links: Wiki https://example.com/wiki" in shown
    assert "How these cases were written." in shown
    assert shown.index("description:") < shown.index("attribution:") < shown.index("license:")

    (root / "sub").mkdir()
    (root / "sub" / "b.yaml").write_text(cases_yaml("rd19-2"))
    (root / "README.md").unlink()
    assert "readme=updated" not in cli.ok("sync", "rd19/sub")
    with api(cli.live, "alice") as alice:
        assert alice.get("/api/datasets/rd19@alice").json()["readme"] is not None

    assert "readme=updated" in cli.ok("sync", "rd19")
    with api(cli.live, "alice") as alice:
        dataset = alice.get("/api/datasets/rd19@alice").json()
        assert (dataset["readme"], dataset["metadata"]) == (None, {})
        assert dataset["last_sync"]["detail"]["readme_changed"] is True


def test_flow_19_a_bad_frontmatter_fails_lint_and_stops_the_sync(cli):
    cli.as_user("alice")
    cli.suite(
        "bad19",
        {"a.yaml": cases_yaml("bad19-1"), "README.md": "---\nlicense: MIT\n---\n# Bad\n"},
    )

    for output in (
        cli.fails("lint", "suites/bad19"),
        cli.fails("lint", "--offline", "suites/bad19"),
        cli.fails("sync", "bad19"),
    ):
        assert "E-README" in output and "license: Input should be a valid dictionary" in output
    with api(cli.live, "alice") as alice:
        assert alice.get("/api/datasets/bad19@alice").status_code == 404
