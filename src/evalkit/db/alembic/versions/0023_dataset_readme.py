"""dataset readme and metadata

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-18

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023"
down_revision = "0022"
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
