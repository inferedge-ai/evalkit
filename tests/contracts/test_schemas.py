import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gen_schemas import generate  # noqa: E402


def test_generated_schemas_current(tmp_path):
    generate(tmp_path)
    committed = ROOT / "contracts"
    names = sorted(p.name for p in committed.glob("*.json"))
    assert names == [
        "atif-accepted.json",
        "custom-evaluator-api-v1.json",
        "harness-api-v1.json",
        "span-mapping-v1.json",
    ]
    for name in names:
        assert (tmp_path / name).read_bytes() == (committed / name).read_bytes(), (
            f"{name} is stale — run: uv run python scripts/gen_schemas.py"
        )
