from evalkit.db.session import downgrade_migrations, run_migrations
from tests.db.test_migration_0016 import _exec, _rows


def test_0021_downgrade_deletes_revoked_tokens(fresh_db_url):
    run_migrations(fresh_db_url, "0021")
    _exec(
        fresh_db_url,
        [
            "INSERT INTO users (id, tenant_id, username, password_hash, is_admin) "
            "VALUES (1, 'default', 'admin', 'x', true)",
            "INSERT INTO workspaces (id, tenant_id, name, owner_user_id) "
            "VALUES (1, 'default', 'admin', 1)",
            "INSERT INTO agents (id, workspace_id, name) VALUES (1, 1, 'mine')",
            "INSERT INTO auth_tokens (id, user_id, token_hash, kind, agent_id) "
            "VALUES (1, 1, 'live', 'agent', 1)",
            "INSERT INTO auth_tokens (id, user_id, token_hash, kind, agent_id, revoked_at, "
            "revoked_by) VALUES (2, 1, 'revoked', 'agent', 1, now(), 1)",
        ],
    )
    downgrade_migrations(fresh_db_url, "0020")
    assert _rows(fresh_db_url, "SELECT id FROM auth_tokens ORDER BY id") == [(1,)]
