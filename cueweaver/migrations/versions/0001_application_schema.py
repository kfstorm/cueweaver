"""Create the application schema, including the #193 compatibility tables."""

from alembic import op

revision = "0001_application_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS adopts databases created by #193, which had no Alembic
    # version table but already contained jobs and app_metadata.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id VARCHAR NOT NULL PRIMARY KEY,
            record_json TEXT NOT NULL,
            created_at VARCHAR NOT NULL,
            queue_sequence INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key VARCHAR NOT NULL PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS term_maps (
            id VARCHAR NOT NULL PRIMARY KEY,
            name VARCHAR NOT NULL,
            name_folded VARCHAR NOT NULL,
            entry_count INTEGER NOT NULL,
            updated_at VARCHAR NOT NULL,
            sequence INTEGER NOT NULL,
            content_json TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_term_maps_name_folded
        ON term_maps (name_folded)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS directory_term_map_bindings (
            directory VARCHAR NOT NULL PRIMARY KEY,
            term_map_id VARCHAR NOT NULL,
            FOREIGN KEY(term_map_id) REFERENCES term_maps(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_directory_term_map_bindings_term_map_id
        ON directory_term_map_bindings (term_map_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS directory_term_map_bindings")
    op.execute("DROP TABLE IF EXISTS term_maps")
    op.execute("DROP TABLE IF EXISTS app_metadata")
    op.execute("DROP TABLE IF EXISTS jobs")
