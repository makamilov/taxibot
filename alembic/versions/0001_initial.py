"""Initial tables."""
from alembic import op
import sqlalchemy as sa
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None
def upgrade() -> None:
    """Create the initial users, routes, and orders tables."""
    op.create_table("users", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True), sa.Column("username", sa.String(64)), sa.Column("full_name", sa.String(255), nullable=False), sa.Column("phone", sa.String(32)), sa.Column("is_driver", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("routes", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("origin", sa.String(100), nullable=False), sa.Column("destination", sa.String(100), nullable=False), sa.Column("base_price", sa.Integer(), nullable=False, server_default="0"), sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.UniqueConstraint("origin", "destination", name="uq_route_origin_destination"))
    op.create_table("orders", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("passenger_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("driver_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("route_id", sa.Integer(), sa.ForeignKey("routes.id"), nullable=False), sa.Column("departure_at", sa.DateTime(timezone=True), nullable=False), sa.Column("seats", sa.Integer(), nullable=False), sa.Column("whole_car", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("trip_type", sa.String(20), nullable=False), sa.Column("phone", sa.String(32), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="pending"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_orders_status", "orders", ["status"])
def downgrade() -> None:
    """Drop the initial tables and order status index."""
    op.drop_index("ix_orders_status", table_name="orders")
    op.drop_table("orders")
    op.drop_table("routes")
    op.drop_table("users")
