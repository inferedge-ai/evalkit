"""langfuse trace source

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16

"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_trace_sources_provider", "trace_sources", type_="check")
    op.create_check_constraint(
        "ck_trace_sources_provider", "trace_sources", "provider IN ('phoenix', 'langfuse')"
    )
    op.add_column("trace_imports", sa.Column("trace_filter", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("trace_imports", "trace_filter")
    op.drop_constraint("ck_trace_sources_provider", "trace_sources", type_="check")
    op.create_check_constraint(
        "ck_trace_sources_provider", "trace_sources", "provider IN ('phoenix')"
    )
