"""case event rejected

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-14

"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_case_events_kind", "case_events", type_="check")
    op.create_check_constraint(
        "ck_case_events_kind",
        "case_events",
        "kind IN ('synced', 'review_requested', 'approved', 'changes_requested', 'rejected')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_case_events_kind", "case_events", type_="check")
    op.create_check_constraint(
        "ck_case_events_kind",
        "case_events",
        "kind IN ('synced', 'review_requested', 'approved', 'changes_requested')",
    )
