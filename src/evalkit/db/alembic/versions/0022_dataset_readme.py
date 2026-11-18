"""dataset readme and metadata

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-18

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("datasets", sa.Column("readme", sa.Text(), nullable=True))
    op.add_column(
        "datasets",
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("datasets", "metadata")
    op.drop_column("datasets", "readme")
