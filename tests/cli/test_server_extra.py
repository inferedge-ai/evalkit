import subprocess
import sys

from evalkit.cli.server_extra import has_server_extra, list_server_requirements

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
    names = list_server_requirements()
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
