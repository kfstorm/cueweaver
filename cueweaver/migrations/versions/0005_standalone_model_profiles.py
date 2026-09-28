"""Replace inheritable Model Profiles with standalone configurations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.schema import SchemaItem

revision = "0005_standalone_model_profiles"
down_revision = "0004_model_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Model Profiles have not been populated in deployed databases. Recreate
    # the two tables directly instead of carrying inheritance data forward.
    op.drop_table("model_profile_settings")
    op.drop_table("model_profiles")
    _create_profile_tables(standalone=True)


def downgrade() -> None:
    op.drop_table("model_profile_settings")
    op.drop_table("model_profiles")
    _create_profile_tables(standalone=False)


def _create_profile_tables(*, standalone: bool) -> None:
    profile_columns: list[SchemaItem] = [
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    ]
    if standalone:
        profile_columns.append(sa.Column("provider", sa.String(), nullable=False))
    else:
        profile_columns.extend(
            (
                sa.Column(
                    "parent_id",
                    sa.String(),
                    sa.ForeignKey("model_profiles.id", ondelete="RESTRICT"),
                ),
                sa.Column("selectable", sa.Boolean(), nullable=False),
            )
        )
    profile_columns.extend(
        (
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
        )
    )
    setting_columns: list[SchemaItem] = [
        sa.Column(
            "profile_id",
            sa.String(),
            sa.ForeignKey("model_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(), primary_key=True),
    ]
    if standalone:
        setting_columns.append(
            sa.Column("value", sa.JSON(none_as_null=True), nullable=False)
        )
    else:
        setting_columns.extend(
            (
                sa.Column("kind", sa.String(), nullable=False),
                sa.Column("value", sa.JSON(none_as_null=True)),
                sa.CheckConstraint(
                    "(kind = 'literal' AND value IS NOT NULL) OR "
                    "(kind = 'unset' AND value IS NULL)",
                    name="model_profile_setting_kind_value",
                ),
            )
        )
    op.create_table("model_profiles", *profile_columns)
    op.create_table("model_profile_settings", *setting_columns)
