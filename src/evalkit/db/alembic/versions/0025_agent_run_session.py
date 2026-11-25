"""agent run session id

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-25

"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("session_id", sa.String(255), nullable=True))
    op.create_index("ix_agent_runs_agent_session", "agent_runs", ["agent_id", "session_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_agent_session", "agent_runs")
    op.drop_column("agent_runs", "session_id")
