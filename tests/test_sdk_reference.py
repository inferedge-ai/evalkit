import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gen_sdk_reference import OUTPUT, generate  # noqa: E402


def test_generated_sdk_reference_current(tmp_path):
    generate(tmp_path / "sdk-reference.md")
    assert (tmp_path / "sdk-reference.md").read_text() == OUTPUT.read_text(), (
        "docs/user/sdk-reference.md is stale — run: uv run python scripts/gen_sdk_reference.py"
    )
