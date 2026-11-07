"""span dropped attributes count

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-06

"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "spans",
        sa.Column("dropped_attributes_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("spans", "dropped_attributes_count")
