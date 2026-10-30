from pathlib import Path

from evalkit import paths

ROOT = Path(__file__).parent.parent


def test_ui_root_in_a_checkout():
    assert paths.ui_root() == ROOT / "ui" / "dist"
