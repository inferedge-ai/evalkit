"""span scope

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-03

"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("spans", sa.Column("scope", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("spans", "scope")
