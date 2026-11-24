"""evaluations on agent runs, agent run kind, app runs cursor

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-24

"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = ("evalkit",)
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "app_runs_stored_until",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() - interval '1 day'"),
        ),
    )
    op.add_column(
        "agent_runs", sa.Column("kind", sa.String(8), nullable=False, server_default="eval")
    )
    op.alter_column("agent_runs", "kind", server_default=None)
    op.create_check_constraint("ck_agent_runs_kind", "agent_runs", "kind IN ('eval', 'app')")
    op.add_column("evaluations", sa.Column("agent_run_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE evaluations SET agent_run_id = experiment_runs.agent_run_id "
        "FROM experiment_runs WHERE experiment_runs.id = evaluations.experiment_run_id"
    )
    op.alter_column("evaluations", "agent_run_id", nullable=False)
    op.create_foreign_key(
        "evaluations_agent_run_id_fkey", "evaluations", "agent_runs", ["agent_run_id"], ["id"]
    )
    op.drop_constraint("uq_evaluations_run_name", "evaluations", type_="unique")
    op.drop_column("evaluations", "experiment_run_id")
    op.create_unique_constraint("uq_evaluations_run_name", "evaluations", ["agent_run_id", "name"])
    op.create_index("ix_evaluations_agent_run_id", "evaluations", ["agent_run_id"])


def downgrade() -> None:
    op.add_column("evaluations", sa.Column("experiment_run_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE evaluations SET experiment_run_id = experiment_runs.id "
        "FROM experiment_runs WHERE experiment_runs.agent_run_id = evaluations.agent_run_id"
    )
    op.execute("DELETE FROM evaluations WHERE experiment_run_id IS NULL")
    op.alter_column("evaluations", "experiment_run_id", nullable=False)
    op.create_foreign_key(
        "evaluations_experiment_run_id_fkey",
        "evaluations",
        "experiment_runs",
        ["experiment_run_id"],
        ["id"],
    )
    op.drop_index("ix_evaluations_agent_run_id", "evaluations")
    op.drop_constraint("uq_evaluations_run_name", "evaluations", type_="unique")
    op.drop_column("evaluations", "agent_run_id")
    op.create_unique_constraint(
        "uq_evaluations_run_name", "evaluations", ["experiment_run_id", "name"]
    )
    op.create_index("ix_evaluations_experiment_run_id", "evaluations", ["experiment_run_id"])
    op.drop_constraint("ck_agent_runs_kind", "agent_runs", type_="check")
    op.drop_column("agent_runs", "kind")
    op.drop_column("agents", "app_runs_stored_until")
