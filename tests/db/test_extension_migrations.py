import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from evalkit.db import session
from evalkit.db.session import (
    downgrade_migrations,
    head_revisions,
    run_migrations,
    verify_schema,
)

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


PACKAGE = "sample_extension"


def install_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    revisions: dict[str, str] | None,
) -> None:
    migrations = tmp_path / PACKAGE / "migrations"
    migrations.mkdir(parents=True)
    (tmp_path / PACKAGE / "__init__.py").touch()
    (migrations / "__init__.py").touch()
    if revisions is not None:
        (migrations / "versions").mkdir()
        for name, text in revisions.items():
            (migrations / "versions" / name).write_text(text)
    dist_info = tmp_path / f"{PACKAGE}-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {PACKAGE}\nVersion: 1.0\n")
    (dist_info / "entry_points.txt").write_text(
        f"[{session.EXTENSION_MIGRATIONS_GROUP}]\nsample = {PACKAGE}.migrations:versions\n"
    )
    for module in [name for name in sys.modules if name.split(".")[0] == PACKAGE]:
        monkeypatch.delitem(sys.modules, module)
    monkeypatch.syspath_prepend(str(tmp_path))


@pytest.fixture
def sample_extension(tmp_path, monkeypatch):
    install_package(tmp_path, monkeypatch, {"sample_0001_notes.py": REVISION})


def test_a_missing_versions_folder_fails_naming_the_entry_point(tmp_path, monkeypatch):
    install_package(tmp_path, monkeypatch, None)
    with pytest.raises(RuntimeError, match="entry point 'sample' .* is not a folder"):
        head_revisions()
    with pytest.raises(RuntimeError, match="entry point 'sample'"):
        run_migrations("postgresql+psycopg://unused/unused")


def test_a_revision_id_evalkit_already_uses_fails(tmp_path, monkeypatch):
    clash = REVISION.replace('revision = "sample_0001"', 'revision = "0024"').replace(
        'depends_on = "0024"', 'depends_on = "0023"'
    )
    install_package(tmp_path, monkeypatch, {"sample_0001_notes.py": clash})
    with pytest.raises(RuntimeError, match="Revision 0024 is present more than once"):
        head_revisions()
    with pytest.raises(RuntimeError, match="Revision 0024 is present more than once"):
        run_migrations("postgresql+psycopg://unused/unused")


def test_a_first_revision_without_a_branch_label_fails(tmp_path, monkeypatch):
    unlabelled = REVISION.replace('branch_labels = ("sample",)', "branch_labels = None")
    install_package(tmp_path, monkeypatch, {"sample_0001_notes.py": unlabelled})
    with pytest.raises(RuntimeError, match="'sample_0001' of entry point 'sample' .* no `branch"):
        head_revisions()


def test_downgrading_a_packages_branch_lets_evalkit_start_without_it(
    fresh_db_url, sample_extension, monkeypatch
):
    run_migrations(fresh_db_url)
    downgrade_migrations(fresh_db_url, "sample@base")
    monkeypatch.setattr(session, "entry_points", lambda group: [])
    asyncio.run(verify_schema(fresh_db_url))


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


def test_the_start_check_names_a_revision_whose_package_is_gone(
    fresh_db_url, sample_extension, monkeypatch
):
    run_migrations(fresh_db_url)
    monkeypatch.setattr(session, "entry_points", lambda group: [])

    with pytest.raises(
        RuntimeError,
        match=r"\['sample_0001'\], which this install does not have.*a newer evalkit",
    ):
        asyncio.run(verify_schema(fresh_db_url))


def test_autogenerate_ignores_a_packages_tables_only_while_it_is_installed(
    sample_extension, monkeypatch
):
    assert not session.include_name("sample_notes", "table", {})
    assert not session.include_name("procrastinate_jobs", "table", {})
    assert session.include_name("agent_runs", "table", {})
    assert session.include_name("ix_sample", "index", {})
    monkeypatch.setattr(session, "entry_points", lambda group: [])
    assert session.include_name("sample_notes", "table", {})
