import asyncio
from importlib.metadata import EntryPoint

import pytest
from sqlalchemy import create_engine, inspect

from evalkit.db import session
from evalkit.db.session import head_revisions, run_migrations, verify_schema

REVISION = """
import sqlalchemy as sa
from alembic import op

revision = "sample_0001"
down_revision = None
branch_labels = ("sample",)
depends_on = "0024"


def upgrade() -> None:
    op.create_table(
        "sample_notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("agent_run_id", sa.Integer, sa.ForeignKey("agent_runs.id"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("sample_notes")
"""


@pytest.fixture
def sample_extension(tmp_path, monkeypatch):
    versions = tmp_path / "sample_extension" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (tmp_path / "sample_extension" / "__init__.py").touch()
    (tmp_path / "sample_extension" / "migrations" / "__init__.py").touch()
    (versions / "sample_0001_notes.py").write_text(REVISION)
    monkeypatch.syspath_prepend(str(tmp_path))
    point = EntryPoint(
        name="sample",
        value="sample_extension.migrations:versions",
        group=session.EXTENSION_MIGRATIONS_GROUP,
    )
    monkeypatch.setattr(session, "entry_points", lambda group: [point])


def test_upgrade_applies_the_branch_of_an_installed_package(fresh_db_url, sample_extension):
    run_migrations(fresh_db_url)
    engine = create_engine(fresh_db_url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "sample_notes" in tables
    assert "sample_0001" in head_revisions()
    asyncio.run(verify_schema(fresh_db_url))


def test_the_start_check_fails_until_the_package_branch_is_applied(
    fresh_db_url, sample_extension, monkeypatch
):
    with monkeypatch.context() as without_the_package:
        without_the_package.setattr(session, "entry_points", lambda group: [])
        run_migrations(fresh_db_url)
    with pytest.raises(RuntimeError, match="evalkit db upgrade"):
        asyncio.run(verify_schema(fresh_db_url))
