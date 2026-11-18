"""extension sync source and evaluation source

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17

"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_dataset_syncs_source", "dataset_syncs", type_="check")
    op.create_check_constraint(
        "ck_dataset_syncs_source", "dataset_syncs", "source IN ('git', 'promotion', 'extension')"
    )
    op.alter_column(
        "evaluations", "source", type_=sa.String(16), existing_type=sa.String(8), nullable=False
    )
    op.drop_constraint("ck_evaluations_source", "evaluations", type_="check")
    op.create_check_constraint(
        "ck_evaluations_source",
        "evaluations",
        "source IN ('builtin', 'remote', 'code', 'extension')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_evaluations_source", "evaluations", type_="check")
    op.create_check_constraint(
        "ck_evaluations_source", "evaluations", "source IN ('builtin', 'remote', 'code')"
    )
    op.alter_column(
        "evaluations", "source", type_=sa.String(8), existing_type=sa.String(16), nullable=False
    )
    op.drop_constraint("ck_dataset_syncs_source", "dataset_syncs", type_="check")
    op.create_check_constraint(
        "ck_dataset_syncs_source", "dataset_syncs", "source IN ('git', 'promotion')"
    )
