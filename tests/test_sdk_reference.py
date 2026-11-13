import sys
from pathlib import Path

import evalkit.sdk

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gen_sdk_reference import OUTPUT, SECTIONS, generate  # noqa: E402


def test_sections_cover_every_sdk_export():
    documented = {path.split(".", 1)[0] for _, paths in SECTIONS for path in paths}
    assert set(evalkit.sdk.__all__) <= documented, (
        f"add to SECTIONS in scripts/gen_sdk_reference.py: "
        f"{sorted(set(evalkit.sdk.__all__) - documented)}"
    )


def test_generated_sdk_reference_current(tmp_path):
    generate(tmp_path / "sdk-reference.md")
    assert (tmp_path / "sdk-reference.md").read_text() == OUTPUT.read_text(), (
        "docs/user/sdk-reference.md is stale — run: uv run python scripts/gen_sdk_reference.py"
    )
