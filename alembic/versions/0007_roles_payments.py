"""Add user roles, driver subscriptions, payment transactions, and DM references."""
from alembic import op
import sqlalchemy as sa

revision = "0007_roles_payments"
down_revision = "0006_driver_message_ref"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create role and subscription schema while preserving existing accounts as clients."""
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), server_default="client", nullable=False),
    )
    op.create_check_constraint("ck_users_role", "users", "role IN ('client', 'driver')")
    op.execute("UPDATE users SET role = 'driver' WHERE is_driver IS TRUE")
    op.create_table(
        "driver_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_trial_used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("driver_id", name="uq_driver_subscription_driver"),
        sa.CheckConstraint("driver_id > 0", name="ck_driver_subscription_driver_positive"),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_tx_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("external_tx_id", name="uq_transaction_external_tx_id"),
        sa.CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        sa.CheckConstraint("duration_days > 0", name="ck_transaction_duration_positive"),
        sa.CheckConstraint("status IN ('pending', 'success', 'failed')", name="ck_transactions_status"),
    )
    op.create_index("ix_transactions_driver_id", "transactions", ["driver_id"])
    op.create_index("ix_transactions_status", "transactions", ["status"])
    op.create_table(
        "driver_order_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("driver_telegram_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint("order_id", "driver_telegram_id", name="uq_order_driver_notification"),
    )
    op.create_index("ix_driver_order_notifications_order_id", "driver_order_notifications", ["order_id"])


def downgrade() -> None:
    """Drop the role and subscription schema introduced by this revision."""
    op.drop_index("ix_driver_order_notifications_order_id", table_name="driver_order_notifications")
    op.drop_table("driver_order_notifications")
    op.drop_index("ix_transactions_status", table_name="transactions")
    op.drop_index("ix_transactions_driver_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("driver_subscriptions")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
