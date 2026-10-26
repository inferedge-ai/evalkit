"""ticket token kind

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-25

"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_auth_tokens_kind", "auth_tokens", type_="check")
    op.create_check_constraint(
        "ck_auth_tokens_kind", "auth_tokens", "kind IN ('user', 'agent', 'ticket')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM auth_tokens WHERE kind = 'ticket'")
    op.drop_constraint("ck_auth_tokens_kind", "auth_tokens", type_="check")
    op.create_check_constraint("ck_auth_tokens_kind", "auth_tokens", "kind IN ('user', 'agent')")
