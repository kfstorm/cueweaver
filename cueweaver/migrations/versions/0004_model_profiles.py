"""Add inheritable Model Profiles and require one per Job."""

import sqlalchemy as sa
from alembic import op

revision = "0004_model_profiles"
down_revision = "0003_retire_job_record_schema_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "parent_id",
            sa.String(),
            sa.ForeignKey("model_profiles.id", ondelete="RESTRICT"),
        ),
        sa.Column("selectable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "model_profile_settings",
        sa.Column(
            "profile_id",
            sa.String(),
            sa.ForeignKey("model_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(none_as_null=True)),
        sa.CheckConstraint(
            "(kind = 'literal' AND value IS NOT NULL) OR (kind = 'unset' AND value IS NULL)",
            name="model_profile_setting_kind_value",
        ),
    )
    # Jobs are empty at this product boundary. A SQLite batch rebuild would
    # discard the existing unnamed CHECK constraints on Job lifecycle fields.
    op.execute(
        "ALTER TABLE jobs ADD COLUMN model_profile_id VARCHAR NOT NULL "
        "REFERENCES model_profiles(id) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("model_profile_id")
    op.drop_table("model_profile_settings")
    op.drop_table("model_profiles")
