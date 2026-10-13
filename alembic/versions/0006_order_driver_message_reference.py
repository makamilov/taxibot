"""Store Telegram identifiers for the driver-facing order card."""
from alembic import op
import sqlalchemy as sa

revision = "0006_driver_message_ref"
down_revision = "0005_passenger_orders_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable driver chat and message identifiers to existing orders."""
    op.add_column("orders", sa.Column("driver_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("driver_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove the stored driver-card identifiers."""
    op.drop_column("orders", "driver_message_id")
    op.drop_column("orders", "driver_chat_id")
