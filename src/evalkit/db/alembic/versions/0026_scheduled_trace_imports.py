"""trace imports started by the schedule have no creator

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-25

"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("trace_imports", "created_by", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM trace_imports WHERE created_by IS NULL")
    op.alter_column("trace_imports", "created_by", existing_type=sa.Integer(), nullable=False)
