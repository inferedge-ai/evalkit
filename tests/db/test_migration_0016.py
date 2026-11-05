import json

import pytest
from sqlalchemy import create_engine, inspect, text

from evalkit.db.session import downgrade_migrations, head_revision, run_migrations


def _exec(url: str, statements: list[str]) -> None:
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for statement in statements:
            conn.execute(text(statement))
    engine.dispose()


def _rows(url: str, sql: str) -> list[tuple]:
    engine = create_engine(url)
    with engine.connect() as conn:
        found = list(conn.execute(text(sql)).all())
    engine.dispose()
    return found


def _seed_at_0015(url: str) -> None:
    judge = json.dumps({"base_url": "https://llm.example", "model": "m"})
    _exec(
        url,
        [
            "INSERT INTO users (id, username, password_hash, is_admin) "
            "VALUES (1, 'admin', 'x', true)",
            "INSERT INTO workspaces (id, name, owner_user_id) VALUES (1, 'admin', 1)",
            "INSERT INTO workspaces (id, name) VALUES (2, 'dev')",
            "INSERT INTO cases (id, case_id) VALUES (1, 'q-1')",
            "INSERT INTO llm_providers (id, name, base_url) VALUES (1, 'p', 'https://llm.example')",
            "INSERT INTO trace_sources (id, name, provider, config) "
            "VALUES (1, 's', 'phoenix', '{}')",
            f"INSERT INTO fixture_blobs (sha256, size) VALUES ('{'a' * 64}', 10)",
            f"INSERT INTO server_settings (id, default_judge) VALUES (1, '{judge}')",
        ],
    )


def test_0016_backfills_tenant_1_and_folds_server_settings(fresh_db_url):
    run_migrations(fresh_db_url, "0015")
    _seed_at_0015(fresh_db_url)
    run_migrations(fresh_db_url, "0016")
    assert head_revision() == "0016"
    [(judge,)] = _rows(
        fresh_db_url, "SELECT default_judge->>'model' FROM tenant_settings WHERE tenant_id = 1"
    )
    assert judge == "m"
    for table in (
        "users",
        "workspaces",
        "cases",
        "llm_providers",
        "trace_sources",
        "fixture_blobs",
    ):
        assert _rows(fresh_db_url, f"SELECT DISTINCT tenant_id FROM {table}") == [(1,)]
    engine = create_engine(fresh_db_url)
    inspector = inspect(engine)
    assert "server_settings" not in inspector.get_table_names()
    assert "tenants" not in inspector.get_table_names()
    assert inspector.get_pk_constraint("fixture_blobs")["constrained_columns"] == [
        "tenant_id",
        "sha256",
    ]
    user_columns = {c["name"]: c for c in inspector.get_columns("users")}
    assert user_columns["password_hash"]["nullable"] is True
    assert {"issuer", "subject", "email"} <= set(user_columns)
    index_names = {i["name"] for i in inspector.get_indexes("users")}
    assert "uq_users_tenant_subject" in index_names
    engine.dispose()


def test_0016_refuses_to_downgrade_with_a_second_tenant(fresh_db_url):
    run_migrations(fresh_db_url, "0016")
    _exec(fresh_db_url, ["INSERT INTO tenant_settings (tenant_id) VALUES (2)"])
    with pytest.raises(RuntimeError, match="tenant"):
        downgrade_migrations(fresh_db_url, "0015")


def test_0016_downgrades_cleanly_with_one_tenant(fresh_db_url):
    run_migrations(fresh_db_url, "0015")
    _seed_at_0015(fresh_db_url)
    run_migrations(fresh_db_url, "0016")
    downgrade_migrations(fresh_db_url, "0015")
    [(judge,)] = _rows(fresh_db_url, "SELECT default_judge->>'model' FROM server_settings")
    assert judge == "m"
    run_migrations(fresh_db_url, "0016")


def test_0016_refuses_to_downgrade_with_a_passwordless_user(fresh_db_url):
    run_migrations(fresh_db_url, "0016")
    _exec(
        fresh_db_url,
        ["INSERT INTO users (tenant_id, username, password_hash) VALUES (1, 'sso', NULL)"],
    )
    with pytest.raises(RuntimeError, match="password"):
        downgrade_migrations(fresh_db_url, "0015")


def test_0016_refuses_to_downgrade_with_a_user_in_a_second_tenant(fresh_db_url):
    run_migrations(fresh_db_url, "0016")
    _exec(
        fresh_db_url,
        ["INSERT INTO users (tenant_id, username, password_hash) VALUES (2, 'eve', 'x')"],
    )
    with pytest.raises(RuntimeError, match="tenant"):
        downgrade_migrations(fresh_db_url, "0015")
