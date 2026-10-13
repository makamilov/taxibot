"""Add separate VIP access and payment type."""
from alembic import op
import sqlalchemy as sa

revision = "0009_vip_driver_access"
down_revision = "0008_driver_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store VIP expiry separately and distinguish VIP transactions."""
    op.add_column(
        "driver_subscriptions",
        sa.Column("vip_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("subscription_type", sa.String(length=16), server_default="regular", nullable=False),
    )
    op.create_check_constraint(
        "ck_transactions_subscription_type",
        "transactions",
        "subscription_type IN ('regular', 'vip')",
    )


def downgrade() -> None:
    """Remove VIP access and transaction classification."""
    op.drop_constraint("ck_transactions_subscription_type", "transactions", type_="check")
    op.drop_column("transactions", "subscription_type")
    op.drop_column("driver_subscriptions", "vip_expires_at")
