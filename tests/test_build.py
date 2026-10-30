import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.mark.skipif(
    not (ROOT / "ui" / "dist" / "index.html").is_file(), reason="ui/dist is not built"
)
def test_wheel_bundles_the_ui(tmp_path):
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    names = zipfile.ZipFile(next(tmp_path.glob("evalkit_py-*.whl"))).namelist()
    assert "evalkit/bundled/ui/index.html" in names
    assert any(name.startswith("evalkit/bundled/ui/assets/") for name in names)
