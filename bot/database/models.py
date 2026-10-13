from datetime import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym

class Base(DeclarativeBase):
    """Declarative base shared by all SQLAlchemy models."""
    pass

class User(Base):
    """A Telegram account that can place orders or be assigned as a driver."""
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('client', 'driver')", name="ck_users_role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(
        "language_code", String(10), default="ru", server_default="ru", nullable=False
    )
    language_code = synonym("language")
    role: Mapped[str] = mapped_column(String(16), default="client", server_default="client", nullable=False)
    is_driver: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_working: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    referrer_telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Route(Base):
    """A configured origin and destination pair with an optional base price."""
    __tablename__ = "routes"
    __table_args__ = (UniqueConstraint("origin", "destination", name="uq_route_origin_destination"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str] = mapped_column(String(100))
    destination: Mapped[str] = mapped_column(String(100))
    base_price: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class Order(Base):
    """A passenger trip request and its current driver and status."""
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_passenger_created_at", "passenger_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    passenger_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"))
    departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    departure_period: Mapped[str] = mapped_column(String(20), default="exact", server_default="exact", nullable=False)
    seats: Mapped[int] = mapped_column(Integer)
    whole_car: Mapped[bool] = mapped_column(Boolean, default=False)
    trip_type: Mapped[str] = mapped_column(String(20))
    phone: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    driver_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    driver_message_id: Mapped[int | None] = mapped_column(Integer)
    passenger: Mapped[User] = relationship(foreign_keys=[passenger_id])
    driver: Mapped[User | None] = relationship(foreign_keys=[driver_id])
    route: Mapped[Route] = relationship()


class DriverSubscription(Base):
    """Paid or trial access period assigned to a driver account."""
    __tablename__ = "driver_subscriptions"
    __table_args__ = (
        UniqueConstraint("driver_id", name="uq_driver_subscription_driver"),
        CheckConstraint("driver_id > 0", name="ck_driver_subscription_driver_positive"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    is_trial_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vip_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DriverRoute(Base):
    """A route selected by a driver as eligible for incoming order notifications."""
    __tablename__ = "driver_routes"
    __table_args__ = (UniqueConstraint("driver_telegram_id", "route_id", name="uq_driver_route"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    driver_telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True
    )
    route_id: Mapped[int] = mapped_column(
        ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)


class DriverReferral(Base):
    """Records one rewarded referral per newly registered driver."""
    __tablename__ = "driver_referrals"
    __table_args__ = (
        UniqueConstraint("invitee_driver_id", name="uq_driver_referral_invitee"),
        CheckConstraint("inviter_driver_id <> invitee_driver_id", name="ck_driver_referral_not_self"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    inviter_driver_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True
    )
    invitee_driver_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Transaction(Base):
    """A pending or settled subscription payment from the configured merchant."""
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("external_tx_id", name="uq_transaction_external_tx_id"),
        CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        CheckConstraint("duration_days > 0", name="ck_transaction_duration_positive"),
        CheckConstraint("status IN ('pending', 'success', 'failed')", name="ck_transactions_status"),
        CheckConstraint("subscription_type IN ('regular', 'vip')", name="ck_transactions_subscription_type"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    driver_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_tx_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    subscription_type: Mapped[str] = mapped_column(String(16), default="regular", server_default="regular", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DriverOrderNotification(Base):
    """References each driver's private Telegram card for later cancellation updates."""
    __tablename__ = "driver_order_notifications"
    __table_args__ = (
        UniqueConstraint("order_id", "driver_telegram_id", name="uq_order_driver_notification"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    driver_telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
