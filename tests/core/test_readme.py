import pytest

from evalkit.core.readme import DatasetReadme, parse_readme

FULL = """---
description: Multi-hop questions.
license:
  name: Apache-2.0
  url: https://www.apache.org/licenses/LICENSE-2.0
attribution: By someone.
links:
  - label: Paper
    url: https://example.com/paper
team: research
---

# Title

Body text.
"""


def test_frontmatter_is_the_metadata_and_the_rest_is_the_readme():
    readme, findings = parse_readme(FULL)

    assert findings == []
    assert readme.readme == "# Title\n\nBody text.\n"
    assert readme.metadata == {
        "description": "Multi-hop questions.",
        "license": {"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"},
        "attribution": "By someone.",
        "links": [{"label": "Paper", "url": "https://example.com/paper"}],
        "team": "research",
    }


def test_a_readme_without_frontmatter_has_no_metadata():
    assert parse_readme("# Title\n") == (DatasetReadme(readme="# Title\n"), [])


def test_frontmatter_alone_leaves_the_readme_empty():
    readme, findings = parse_readme("---\ndescription: Short.\n---\n")

    assert (readme.readme, readme.metadata, findings) == (None, {"description": "Short."}, [])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\ndescription: x\n# Title\n", "the frontmatter cannot be read"),
        ("---\n- a\n- b\n---\n", "the frontmatter must be a mapping"),
        ("---\ndescription: [a]\n---\n", "description: Input should be a valid string"),
        ("---\nlicense: MIT\n---\n", "license: Input should be a valid dictionary"),
        ("---\nlinks:\n  - url: https://x\n---\n", "links.0.label: Field required"),
        ("---\nteam:\n  size: 3\n---\n", "team: Input should be a valid string"),
        ("---\nkey: [unclosed\n---\n", "the frontmatter cannot be read"),
    ],
    ids=[
        "never closed",
        "a list",
        "description not text",
        "licence not a mapping",
        "link without a label",
        "another key that is not text",
        "broken YAML",
    ],
)
def test_a_bad_frontmatter_is_an_error_finding(text, message):
    readme, findings = parse_readme(text)

    assert readme == DatasetReadme()
    assert [(f.severity, f.code, f.case_id) for f in findings] == [
        ("error", "E-README", "README.md")
    ]
    assert findings[0].message.startswith(message), findings[0].message
