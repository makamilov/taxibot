"""Add driver work status, route preferences, and referral rewards."""
from alembic import op
import sqlalchemy as sa

revision = "0008_driver_preferences"
down_revision = "0007_roles_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the fields and tables used by driver controls and referral bonuses."""
    op.add_column(
        "users",
        sa.Column("is_working", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("referrer_telegram_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_referrer_telegram_id_users",
        "users",
        "users",
        ["referrer_telegram_id"],
        ["telegram_id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "driver_routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("driver_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["driver_telegram_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("driver_telegram_id", "route_id", name="uq_driver_route"),
    )
    op.create_index("ix_driver_routes_driver_telegram_id", "driver_routes", ["driver_telegram_id"])
    op.create_index("ix_driver_routes_route_id", "driver_routes", ["route_id"])
    op.execute(
        """
        UPDATE routes SET is_active = TRUE WHERE (origin, destination) IN (
            ('Бишкек', 'Ош'), ('Ош', 'Бишкек'),
            ('Бишкек', 'Балыкчы'), ('Балыкчы', 'Бишкек'),
            ('Бишкек', 'Чолпон-Ата'), ('Чолпон-Ата', 'Бишкек'),
            ('Бишкек', 'Талас'), ('Талас', 'Бишкек'),
            ('Бишкек', 'Нарын'), ('Нарын', 'Бишкек'),
            ('Бишкек', 'Каракол'), ('Каракол', 'Бишкек')
        )
        """
    )
    op.execute(
        """
        INSERT INTO driver_routes (driver_telegram_id, route_id)
        SELECT users.telegram_id, routes.id
        FROM users CROSS JOIN routes
        WHERE users.role = 'driver' AND routes.is_active IS TRUE
        ON CONFLICT (driver_telegram_id, route_id) DO NOTHING
        """
    )
    op.create_table(
        "driver_referrals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("inviter_driver_id", sa.BigInteger(), nullable=False),
        sa.Column("invitee_driver_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["inviter_driver_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invitee_driver_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("invitee_driver_id", name="uq_driver_referral_invitee"),
        sa.CheckConstraint("inviter_driver_id <> invitee_driver_id", name="ck_driver_referral_not_self"),
    )
    op.create_index("ix_driver_referrals_inviter_driver_id", "driver_referrals", ["inviter_driver_id"])


def downgrade() -> None:
    """Remove the driver settings and referral tables added in this revision."""
    op.drop_index("ix_driver_referrals_inviter_driver_id", table_name="driver_referrals")
    op.drop_table("driver_referrals")
    op.drop_index("ix_driver_routes_route_id", table_name="driver_routes")
    op.drop_index("ix_driver_routes_driver_telegram_id", table_name="driver_routes")
    op.drop_table("driver_routes")
    op.drop_constraint("fk_users_referrer_telegram_id_users", "users", type_="foreignkey")
    op.drop_column("users", "referrer_telegram_id")
    op.drop_column("users", "is_working")
