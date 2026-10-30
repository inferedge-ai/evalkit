"""keys and llm providers

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _audit() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.add_column("agents", sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True))
    op.add_column(
        "remote_evaluators", sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True)
    )
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        *_audit(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_providers_name"),
        sa.UniqueConstraint("base_url", name="uq_llm_providers_base_url"),
    )
    op.create_table(
        "server_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("default_judge", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_audit(),
        sa.CheckConstraint("id = 1", name="ck_server_settings_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("server_settings")
    op.drop_table("llm_providers")
    op.drop_column("remote_evaluators", "api_key_encrypted")
    op.drop_column("agents", "api_key_encrypted")
