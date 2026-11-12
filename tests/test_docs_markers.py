import importlib.util
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
