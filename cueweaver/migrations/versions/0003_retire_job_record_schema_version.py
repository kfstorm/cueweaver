"""Remove the obsolete Job-record schema version column."""

from alembic import op

revision = "0003_retire_job_record_schema_version"
down_revision = "0002_normalize_relational_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("jobs", "schema_version")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE jobs ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1 "
        "CHECK (schema_version >= 1)"
    )
