"""agent run trajectory note

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-13

"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("trajectory_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "trajectory_note")
