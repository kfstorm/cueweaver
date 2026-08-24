"""Create the application schema, including the #193 compatibility tables."""

from alembic import op

from cueweaver.migrations.legacy_schema import create_legacy_schema

revision = "0001_application_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS adopts databases created by #193, which had no Alembic
    # version table but already contained jobs and app_metadata.
    create_legacy_schema(op.execute)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS directory_term_map_bindings")
    op.execute("DROP TABLE IF EXISTS term_maps")
    op.execute("DROP TABLE IF EXISTS app_metadata")
    op.execute("DROP TABLE IF EXISTS jobs")
