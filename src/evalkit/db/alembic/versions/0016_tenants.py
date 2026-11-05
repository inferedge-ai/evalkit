"""tenant ids and tenant settings

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-04

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


ROOT_TABLES = ["users", "workspaces", "cases", "llm_providers", "trace_sources", "fixture_blobs"]

OLD_UNIQUES = {
    "workspaces": ["uq_workspaces_name"],
    "cases": ["uq_cases_case_id"],
    "llm_providers": ["uq_llm_providers_name", "uq_llm_providers_base_url"],
    "trace_sources": ["uq_trace_sources_name"],
}

NEW_UNIQUES = {
    "users": [("uq_users_tenant_username", ["tenant_id", "username"])],
    "workspaces": [("uq_workspaces_tenant_name", ["tenant_id", "name"])],
    "cases": [("uq_cases_tenant_case_id", ["tenant_id", "case_id"])],
    "llm_providers": [
        ("uq_llm_providers_tenant_name", ["tenant_id", "name"]),
        ("uq_llm_providers_tenant_base_url", ["tenant_id", "base_url"]),
    ],
    "trace_sources": [("uq_trace_sources_tenant_name", ["tenant_id", "name"])],
}


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("default_judge", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.execute(
        "INSERT INTO tenant_settings (tenant_id, default_judge) "
        "SELECT 1, default_judge FROM server_settings WHERE id = 1"
    )
    op.drop_table("server_settings")

    for table in ROOT_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True))
        op.execute(f"UPDATE {table} SET tenant_id = 1")
        op.alter_column(table, "tenant_id", nullable=False)

    for table, names in OLD_UNIQUES.items():
        for name in names:
            op.drop_constraint(name, table, type_="unique")
    op.drop_index("ix_users_username", table_name="users")
    for table, uniques in NEW_UNIQUES.items():
        for name, columns in uniques:
            op.create_unique_constraint(name, table, columns)

    op.add_column("case_version_files", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE case_version_files SET tenant_id = 1")
    op.alter_column("case_version_files", "tenant_id", nullable=False)
    op.drop_constraint("case_version_files_sha256_fkey", "case_version_files", type_="foreignkey")
    op.drop_constraint("fixture_blobs_pkey", "fixture_blobs", type_="primary")
    op.create_primary_key("fixture_blobs_pkey", "fixture_blobs", ["tenant_id", "sha256"])
    op.create_foreign_key(
        "fk_case_version_files_blob",
        "case_version_files",
        "fixture_blobs",
        ["tenant_id", "sha256"],
        ["tenant_id", "sha256"],
    )

    op.add_column("users", sa.Column("issuer", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("subject", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.alter_column("users", "password_hash", existing_type=sa.String(length=200), nullable=True)
    op.create_index(
        "uq_users_tenant_subject",
        "users",
        ["tenant_id", "issuer", "subject"],
        unique=True,
        postgresql_where=sa.text("subject IS NOT NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    others = bind.execute(
        sa.text(
            " UNION ".join(
                f"SELECT tenant_id FROM {table} WHERE tenant_id <> 1" for table in ROOT_TABLES
            )
            + " UNION SELECT tenant_id FROM tenant_settings WHERE tenant_id <> 1"
        )
    ).all()
    if others:
        raise RuntimeError(
            f"{len(others)} tenant(s) other than the default have data; 0016 cannot be downgraded"
        )
    passwordless = bind.execute(
        sa.text("SELECT count(*) FROM users WHERE password_hash IS NULL")
    ).scalar_one()
    if passwordless:
        raise RuntimeError(f"{passwordless} user(s) have no password; 0016 cannot be downgraded")
    op.drop_index("uq_users_tenant_subject", table_name="users")
    op.alter_column("users", "password_hash", existing_type=sa.String(length=200), nullable=False)
    op.drop_column("users", "email")
    op.drop_column("users", "subject")
    op.drop_column("users", "issuer")

    op.drop_constraint("fk_case_version_files_blob", "case_version_files", type_="foreignkey")
    op.drop_constraint("fixture_blobs_pkey", "fixture_blobs", type_="primary")
    op.create_primary_key("fixture_blobs_pkey", "fixture_blobs", ["sha256"])
    op.create_foreign_key(
        "case_version_files_sha256_fkey",
        "case_version_files",
        "fixture_blobs",
        ["sha256"],
        ["sha256"],
    )
    op.drop_column("case_version_files", "tenant_id")

    for table, uniques in NEW_UNIQUES.items():
        for name, _ in uniques:
            op.drop_constraint(name, table, type_="unique")
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_unique_constraint("uq_workspaces_name", "workspaces", ["name"])
    op.create_unique_constraint("uq_cases_case_id", "cases", ["case_id"])
    op.create_unique_constraint("uq_llm_providers_name", "llm_providers", ["name"])
    op.create_unique_constraint("uq_llm_providers_base_url", "llm_providers", ["base_url"])
    op.create_unique_constraint("uq_trace_sources_name", "trace_sources", ["name"])

    for table in ROOT_TABLES:
        op.drop_column(table, "tenant_id")

    op.create_table(
        "server_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("default_judge", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_server_settings_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(
        "INSERT INTO server_settings (id, default_judge) "
        "SELECT 1, (SELECT default_judge FROM tenant_settings WHERE tenant_id = 1)"
    )
    op.drop_table("tenant_settings")
