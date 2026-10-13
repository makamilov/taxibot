"""Speed up passenger order history queries."""
from alembic import op

revision = "0005_passenger_orders_index"
down_revision = "0004_language_optional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a composite index for passenger order history and newest-first sorting."""
    op.create_index(
        "ix_orders_passenger_created_at",
        "orders",
        ["passenger_id", "created_at"],
    )


def downgrade() -> None:
    """Remove the passenger order history index."""
    op.drop_index("ix_orders_passenger_created_at", table_name="orders")
