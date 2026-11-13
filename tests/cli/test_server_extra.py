import importlib.metadata
import subprocess
import sys

from evalkit import version
from evalkit.cli.server_extra import (
    _SERVER_MARKER,
    _list_server_requirements,
    has_server_extra,
)

WITHOUT_BCRYPT = """
import importlib.metadata
import sys

real = importlib.metadata.distributions
importlib.metadata.distributions = lambda: [
    d for d in real() if (d.metadata["Name"] or "").lower() != "bcrypt"
]
sys.modules["bcrypt"] = None

from typer.testing import CliRunner

from evalkit.cli.main import app

runner = CliRunner()
help_result = runner.invoke(app, ["--help"])
print("help", help_result.exit_code)
serve_result = runner.invoke(app, ["serve"])
print("serve", serve_result.exit_code)
print(serve_result.output)
"""


def test_server_requirements_come_from_the_extra() -> None:
    names = _list_server_requirements()
    assert {"bcrypt", "uvicorn", "sqlalchemy", "fastapi"} <= set(names)
    assert "typer" not in names


def test_server_extra_needs_every_requirement() -> None:
    assert has_server_extra(["uvicorn", "sqlalchemy"], {"uvicorn", "sqlalchemy"})
    assert not has_server_extra(["uvicorn", "sqlalchemy", "bcrypt"], {"uvicorn", "sqlalchemy"})
    assert not has_server_extra([], {"uvicorn"})


def test_base_cli_runs_with_uvicorn_and_sqlalchemy_but_no_bcrypt() -> None:
    result = subprocess.run(
        [sys.executable, "-c", WITHOUT_BCRYPT], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "help 0" in result.stdout
    assert "serve 1" in result.stdout
    assert "evalkit-py[server]" in result.stdout


def test_full_dev_install_has_the_server_extra() -> None:
    assert _list_server_requirements()
    assert has_server_extra()


def test_server_requirements_carry_only_the_extra_marker() -> None:
    requires = importlib.metadata.requires(version.DISTRIBUTION) or []
    markers = [r.partition(";")[2].strip() for r in requires if "extra ==" in r]
    server = [m for m in markers if _SERVER_MARKER.search(m)]
    assert server
    others = [m for m in server if _SERVER_MARKER.sub("", m).strip()]
    assert not others, (
        f"server requirements carry other markers {others}; switch _list_server_requirements to "
        'packaging.requirements.Requirement(...).marker.evaluate({"extra": "server"})'
    )
