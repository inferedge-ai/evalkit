"""evaluations.scored_on; provider judges stored by name

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-06

"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evaluations", sa.Column("scored_on", sa.String(8), nullable=True))
    op.execute(
        """
        UPDATE evaluations e
        SET scored_on = CASE WHEN w.owner_user_id IS NULL THEN 'server' ELSE 'local' END
        FROM experiment_runs er
        JOIN experiments x ON x.id = er.experiment_id
        JOIN agents a ON a.id = x.agent_id
        JOIN workspaces w ON w.id = a.workspace_id
        WHERE er.id = e.experiment_run_id
        """
    )
    op.execute("UPDATE evaluations SET scored_on = 'local' WHERE scored_on IS NULL")
    op.alter_column("evaluations", "scored_on", nullable=False)
    op.create_check_constraint(
        "ck_evaluations_scored_on", "evaluations", "scored_on IN ('local', 'server')"
    )

    bind = op.get_bind()
    unmatched = bind.execute(
        sa.text(
            """
            SELECT s.tenant_id, s.default_judge->>'base_url'
            FROM tenant_settings s
            WHERE s.default_judge ? 'base_url'
              AND NOT EXISTS (
                SELECT 1 FROM llm_providers p
                WHERE p.tenant_id = s.tenant_id
                  AND p.base_url = s.default_judge->>'base_url'
              )
            """
        )
    ).all()
    if unmatched:
        listed = ", ".join(f"{tenant}: {url}" for tenant, url in unmatched)
        raise RuntimeError(
            f"the default judge's base URL matches no LLM provider ({listed}); "
            "register a provider for it, or clear the default judge, then migrate again"
        )
    op.execute(
        """
        UPDATE tenant_settings s
        SET default_judge = (s.default_judge - 'base_url')
            || jsonb_build_object('provider', p.name)
        FROM llm_providers p
        WHERE s.default_judge ? 'base_url'
          AND p.tenant_id = s.tenant_id
          AND p.base_url = s.default_judge->>'base_url'
        """
    )
    op.execute(
        """
        UPDATE experiments x
        SET config = jsonb_set(
            x.config,
            '{judge}',
            ((x.config->'judge') - 'base_url') || jsonb_build_object('provider', p.name)
        )
        FROM agents a, workspaces w, llm_providers p
        WHERE a.id = x.agent_id
          AND w.id = a.workspace_id
          AND w.owner_user_id IS NULL
          AND x.config->'judge' ? 'base_url'
          AND p.tenant_id = w.tenant_id
          AND p.base_url = x.config->'judge'->>'base_url'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE experiments x
        SET config = jsonb_set(
            x.config,
            '{judge}',
            ((x.config->'judge') - 'provider') || jsonb_build_object('base_url', p.base_url)
        )
        FROM agents a, workspaces w, llm_providers p
        WHERE a.id = x.agent_id
          AND w.id = a.workspace_id
          AND x.config->'judge' ? 'provider'
          AND p.tenant_id = w.tenant_id
          AND p.name = x.config->'judge'->>'provider'
        """
    )
    op.execute(
        """
        UPDATE tenant_settings s
        SET default_judge = (s.default_judge - 'provider')
            || jsonb_build_object('base_url', p.base_url)
        FROM llm_providers p
        WHERE s.default_judge ? 'provider'
          AND p.tenant_id = s.tenant_id
          AND p.name = s.default_judge->>'provider'
        """
    )
    op.drop_constraint("ck_evaluations_scored_on", "evaluations", type_="check")
    op.drop_column("evaluations", "scored_on")
