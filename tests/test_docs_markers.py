import importlib.util
import re
from pathlib import Path

import pytest

MARKERS_PATH = Path(__file__).parent.parent / "docs" / "site" / "markers.py"
_spec = importlib.util.spec_from_file_location("docs_markers", MARKERS_PATH)
assert _spec and _spec.loader
markers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(markers)


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Case", "case"),
        ("App run", "app-run"),
        ("Trace, span, session", "trace-span-session"),
        ("`evalkit.yaml`", "evalkit-yaml"),
    ],
)
def test_slugify_row(text, slug):
    assert markers.slugify_row(text) == slug


def test_convert_tabs_makes_material_tabs_and_keeps_code_headings():
    source = "\n".join(
        [
            "#### Outside",
            "<!-- tabs -->",
            "#### CLI",
            "",
            "```bash",
            "#### not a tab",
            "evalkit run",
            "```",
            "",
            "#### UI",
            "",
            "Open the page.",
            "<!-- /tabs -->",
            "After.",
        ]
    )
    assert markers.convert_tabs(source) == "\n".join(
        [
            "#### Outside",
            "",
            '=== "CLI"',
            "",
            "",
            "    ```bash",
            "    #### not a tab",
            "    evalkit run",
            "    ```",
            "",
            "",
            '=== "UI"',
            "",
            "",
            "    Open the page.",
            "",
            "After.",
        ]
    )


def test_convert_tabs_refuses_an_open_block():
    with pytest.raises(ValueError):
        markers.convert_tabs("<!-- tabs -->\n#### CLI\ntext")


def test_anchor_rows_adds_an_id_to_each_body_row():
    source = "\n".join(
        [
            "<!-- anchor-rows -->",
            "| Term | Meaning |",
            "| -- | -- |",
            "| App run | One |",
            "| `evalkit.yaml` | Two |",
            "",
            "| Plain | Table |",
        ]
    )
    assert markers.anchor_rows(source) == "\n".join(
        [
            "| Term | Meaning |",
            "| -- | -- |",
            '| <span id="app-run" class="ek-row-anchor"></span> App run | One |',
            '| <span id="evalkit-yaml" class="ek-row-anchor"></span> `evalkit.yaml` | Two |',
            "",
            "| Plain | Table |",
        ]
    )


@pytest.mark.parametrize(
    ("cell", "slug"),
    [
        ("[App run](runs.md#x)", "app-run"),
        ("*Trace* **source**", "trace-source"),
        ("`evalkit.yaml`", "evalkit-yaml"),
    ],
)
def test_anchor_rows_slugs_the_rendered_text_of_the_first_cell(cell, slug):
    source = f"<!-- anchor-rows -->\n| A | B |\n| -- | -- |\n| {cell} | x |"
    assert f'id="{slug}"' in markers.anchor_rows(source)


@pytest.mark.parametrize("rows", [["| Case | a |", "| case | b |"], ["| `` | a |"]])
def test_anchor_rows_refuses_repeated_or_empty_ids(rows):
    source = "\n".join(["<!-- anchor-rows -->", "| A | B |", "| -- | -- |", *rows])
    with pytest.raises(ValueError):
        markers.anchor_rows(source)


def test_concepts_rows_have_unique_ids():
    concepts = (Path(__file__).parent.parent / "docs" / "user" / "concepts.md").read_text()
    ids = re.findall(r'<span id="([^"]+)"', markers.anchor_rows(concepts))
    assert {"app-run", "trace-source", "preset", "hosted-evalkit", "evalkit-yaml"} <= set(ids)
