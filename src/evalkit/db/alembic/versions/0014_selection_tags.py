"""selection tags

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-02

"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE experiments
        SET config = jsonb_set(
            config,
            '{selection}',
            ((config->'selection') - 'tag')
                || jsonb_build_object('tags', jsonb_build_array(config->'selection'->'tag'))
        )
        WHERE config->'selection' ? 'tag'
          AND jsonb_typeof(config->'selection'->'tag') = 'string'
        """
    )
    op.execute(
        """
        UPDATE experiments
        SET config = jsonb_set(config, '{selection}', (config->'selection') - 'tag')
        WHERE config->'selection' ? 'tag'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE experiments
        SET config = jsonb_set(
            config,
            '{selection}',
            ((config->'selection') - 'tags')
                || jsonb_build_object('tag', config->'selection'->'tags'->0)
        )
        WHERE jsonb_typeof(config->'selection'->'tags') = 'array'
        """
    )
