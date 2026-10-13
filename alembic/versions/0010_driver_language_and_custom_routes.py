"""Rename the language column and identify driver-added route preferences."""
from alembic import op
import sqlalchemy as sa

revision = "0010_driver_lang_routes"
down_revision = "0009_vip_driver_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve user languages and add per-driver custom route metadata."""
    op.execute("UPDATE users SET language = 'ru' WHERE language IS NULL")
    op.alter_column(
        "users",
        "language",
        new_column_name="language_code",
        existing_type=sa.String(length=2),
    )
    op.alter_column(
        "users",
        "language_code",
        type_=sa.String(length=10),
        existing_type=sa.String(length=2),
        nullable=False,
        server_default="ru",
    )
    op.add_column(
        "driver_routes",
        sa.Column("is_custom", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        """
        UPDATE driver_routes AS preference
        SET is_custom = TRUE
        FROM routes AS route
        WHERE preference.route_id = route.id AND route.is_active IS FALSE
        """
    )


def downgrade() -> None:
    """Restore the old language column and remove custom-route metadata."""
    op.drop_column("driver_routes", "is_custom")
    op.alter_column(
        "users",
        "language_code",
        type_=sa.String(length=2),
        existing_type=sa.String(length=10),
    )
    op.alter_column(
        "users",
        "language_code",
        new_column_name="language",
        existing_type=sa.String(length=2),
        nullable=True,
        server_default=None,
    )
