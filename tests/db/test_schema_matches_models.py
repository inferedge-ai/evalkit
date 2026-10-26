from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from evalkit.db.models import Base


def test_migrations_match_models(db_url):
    engine = create_engine(db_url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_server_default": False})
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == []
