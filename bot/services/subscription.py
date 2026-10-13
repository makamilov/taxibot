"""Business rules for driver trials, paid access, and eligible driver fan-out."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from bot.database.models import DriverReferral, DriverSubscription, User
from bot.database.requests import get_active_drivers_ids as query_active_driver_ids


async def start_trial_if_needed(session, driver_telegram_id: int) -> tuple[datetime, bool]:
    """Create a one-time 72-hour driver trial, or return the existing expiry unchanged."""
    role = await session.scalar(
        select(User.role).where(User.telegram_id == driver_telegram_id)
    )
    if role != "driver":
        raise ValueError("Only driver accounts can receive a trial")
    now = datetime.now(UTC)
    expiry = now + timedelta(hours=72)
    statement = (
        insert(DriverSubscription)
        .values(driver_id=driver_telegram_id, is_trial_used=True, expires_at=expiry)
        .on_conflict_do_nothing(index_elements=[DriverSubscription.driver_id])
        .returning(DriverSubscription.expires_at)
    )
    created_expiry = await session.scalar(statement)
    if created_expiry is not None:
        return created_expiry, True
    current_expiry = await session.scalar(
        select(DriverSubscription.expires_at).where(
            DriverSubscription.driver_id == driver_telegram_id
        )
    )
    if current_expiry is None:
        raise RuntimeError("Driver subscription could not be initialized")
    return current_expiry, False


async def subscription_expiry(session, driver_telegram_id: int) -> datetime | None:
    """Return the stored end time for a driver's subscription, if one exists."""
    return await session.scalar(
        select(DriverSubscription.expires_at).where(
            DriverSubscription.driver_id == driver_telegram_id
        )
    )


async def subscription_is_active(session, driver_telegram_id: int) -> bool:
    """Check active access using PostgreSQL's current timestamp."""
    active = await session.scalar(
        select(DriverSubscription.id)
        .join(User, User.telegram_id == DriverSubscription.driver_id)
        .where(
            User.telegram_id == driver_telegram_id,
            User.role == "driver",
            DriverSubscription.expires_at > func.now(),
        )
        .limit(1)
    )
    return active is not None


async def extend_subscription(session, driver_telegram_id: int, days: int) -> datetime:
    """Atomically extend access from its future expiry or from now after expiration."""
    if days not in {2, 7, 30}:
        raise ValueError("Unsupported subscription duration")
    now = datetime.now(UTC)
    future_expiry = now + timedelta(days=days)
    extension = timedelta(days=days)
    statement = (
        insert(DriverSubscription)
        .values(
            driver_id=driver_telegram_id,
            is_trial_used=True,
            expires_at=future_expiry,
        )
        .on_conflict_do_update(
            index_elements=[DriverSubscription.driver_id],
            set_={
                "is_trial_used": True,
                "expires_at": case(
                    (DriverSubscription.expires_at > now, DriverSubscription.expires_at + extension),
                    else_=future_expiry,
                ),
            },
        )
        .returning(DriverSubscription.expires_at)
    )
    result = await session.scalar(statement)
    if result is None:
        raise RuntimeError("Subscription extension returned no expiry")
    return result


async def vip_subscription_expiry(session, driver_telegram_id: int) -> datetime | None:
    """Return the stored VIP expiry for a driver, if one exists."""
    return await session.scalar(
        select(DriverSubscription.vip_expires_at).where(
            DriverSubscription.driver_id == driver_telegram_id
        )
    )


async def vip_subscription_is_active(session, driver_telegram_id: int) -> bool:
    """Check VIP access against PostgreSQL's current timestamp."""
    result = await session.scalar(
        select(DriverSubscription.id)
        .join(User, User.telegram_id == DriverSubscription.driver_id)
        .where(
            User.telegram_id == driver_telegram_id,
            User.role == "driver",
            DriverSubscription.vip_expires_at > func.now(),
        )
        .limit(1)
    )
    return result is not None


async def extend_vip_subscription(session, driver_telegram_id: int, days: int) -> datetime:
    """Atomically extend VIP access from its future expiry or from the current time."""
    if days not in {7, 30}:
        raise ValueError("Unsupported VIP subscription duration")
    now = datetime.now(UTC)
    future_expiry = now + timedelta(days=days)
    extension = timedelta(days=days)
    statement = (
        insert(DriverSubscription)
        .values(
            driver_id=driver_telegram_id,
            is_trial_used=True,
            expires_at=now,
            vip_expires_at=future_expiry,
        )
        .on_conflict_do_update(
            index_elements=[DriverSubscription.driver_id],
            set_={
                "vip_expires_at": case(
                    (DriverSubscription.vip_expires_at > now, DriverSubscription.vip_expires_at + extension),
                    else_=future_expiry,
                ),
            },
        )
        .returning(DriverSubscription.vip_expires_at)
    )
    result = await session.scalar(statement)
    if result is None:
        raise RuntimeError("VIP subscription extension returned no expiry")
    return result


async def get_active_drivers_ids(session) -> list[int]:
    """Return only driver Telegram IDs with a currently active subscription."""
    return await query_active_driver_ids(session)


async def reward_driver_referral(session, invitee_telegram_id: int) -> tuple[int, datetime] | None:
    """Award a one-time two-day bonus to a driver who invited a new driver."""
    inviter_id = await session.scalar(
        select(User.referrer_telegram_id).where(
            User.telegram_id == invitee_telegram_id,
            User.role == "driver",
        )
    )
    if inviter_id is None or inviter_id == invitee_telegram_id:
        return None
    inviter_is_driver = await session.scalar(
        select(User.telegram_id).where(User.telegram_id == inviter_id, User.role == "driver")
    )
    if inviter_is_driver is None:
        return None
    statement = (
        insert(DriverReferral)
        .values(inviter_driver_id=inviter_id, invitee_driver_id=invitee_telegram_id)
        .on_conflict_do_nothing(index_elements=[DriverReferral.invitee_driver_id])
        .returning(DriverReferral.id)
    )
    referral_id = await session.scalar(statement)
    if referral_id is None:
        return None
    new_expiry = await extend_subscription(session, inviter_id, 2)
    return inviter_id, new_expiry
