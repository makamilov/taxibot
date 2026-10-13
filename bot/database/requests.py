"""Database access functions for users, routes, orders, and admin reports."""
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import joinedload

from bot.database.models import DriverOrderNotification, DriverRoute, DriverSubscription, Order, Route, User

DEFAULT_ROUTES = (
    ("Бишкек", "Ош"),
    ("Ош", "Бишкек"),
    ("Бишкек", "Балыкчы"),
    ("Балыкчы", "Бишкек"),
    ("Бишкек", "Чолпон-Ата"),
    ("Чолпон-Ата", "Бишкек"),
    ("Бишкек", "Талас"),
    ("Талас", "Бишкек"),
    ("Бишкек", "Нарын"),
    ("Нарын", "Бишкек"),
    ("Бишкек", "Каракол"),
    ("Каракол", "Бишкек"),
)


async def upsert_user(
    session,
    tg_user,
    phone: str | None = None,
    language: str | None = None,
    referrer_telegram_id: int | None = None,
) -> User:
    """Insert or update a Telegram user with a single race-safe SQL statement."""
    values = {
        "telegram_id": tg_user.id,
        "username": tg_user.username,
        "full_name": (tg_user.full_name or "")[:255],
        "phone": phone,
        "language_code": language if language in {"ru", "ky"} else "ru",
        "referrer_telegram_id": referrer_telegram_id,
    }
    statement = insert(User).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[User.telegram_id],
        set_={
            "username": statement.excluded.username,
            "full_name": statement.excluded.full_name,
            "phone": func.coalesce(statement.excluded.phone, User.phone),
            "language_code": func.coalesce(statement.excluded.language_code, User.language_code),
            "referrer_telegram_id": func.coalesce(User.referrer_telegram_id, statement.excluded.referrer_telegram_id),
        },
    ).returning(User)
    return await session.scalar(statement)


async def get_user(session, telegram_id: int) -> User | None:
    """Fetch one user by the unique Telegram account ID."""
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def set_user_role(session, telegram_id: int, role: str) -> bool:
    """Persist one of the supported roles for a Telegram user."""
    if role not in {"client", "driver"}:
        return False
    result = await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(role=role)
    )
    return result.rowcount == 1


async def get_active_drivers_ids(session) -> list[int]:
    """Return driver Telegram IDs whose subscription is active according to PostgreSQL time."""
    statement = (
        select(User.telegram_id)
        .join(DriverSubscription, DriverSubscription.driver_id == User.telegram_id)
        .where(User.role == "driver", DriverSubscription.expires_at > func.now())
        .order_by(User.telegram_id)
    )
    return list((await session.scalars(statement)).all())


async def get_active_drivers_count(
    session,
    from_city: str,
    to_city: str,
    *,
    vip_only: bool = False,
) -> int:
    """Count online, subscribed drivers registered for the requested route.

    This project uses ``role='driver'`` for registered drivers and
    ``is_working`` for their online/available toggle. It has no separate
    approval or online columns, so the count follows the same eligibility
    rules as order fan-out, including the driver's route preferences.
    """
    statement = (
        select(func.count(func.distinct(User.telegram_id)))
        .select_from(User)
        .join(DriverSubscription, DriverSubscription.driver_id == User.telegram_id)
        .join(DriverRoute, DriverRoute.driver_telegram_id == User.telegram_id)
        .join(Route, Route.id == DriverRoute.route_id)
        .where(
            User.role == "driver",
            User.is_working.is_(True),
            DriverSubscription.expires_at > func.now(),
            Route.is_active.is_(True),
            func.lower(func.trim(Route.origin)) == from_city.strip().lower(),
            func.lower(func.trim(Route.destination)) == to_city.strip().lower(),
        )
    )
    if vip_only:
        statement = statement.where(DriverSubscription.vip_expires_at > func.now())
    return int((await session.scalar(statement)) or 0)


async def get_driver_routes(session, driver_telegram_id: int) -> list[Route]:
    """Load selected route records for one driver in stable order."""
    result = await session.scalars(
        select(Route)
        .join(DriverRoute, DriverRoute.route_id == Route.id)
        .where(DriverRoute.driver_telegram_id == driver_telegram_id)
        .order_by(Route.origin, Route.destination)
    )
    return list(result.all())


async def toggle_driver_route(session, driver_telegram_id: int, route_id: int) -> bool | None:
    """Toggle a driver's route choice; return None when the route is unavailable."""
    deleted = await session.execute(
        delete(DriverRoute).where(
            DriverRoute.driver_telegram_id == driver_telegram_id,
            DriverRoute.route_id == route_id,
        )
    )
    if deleted.rowcount:
        return False
    route_exists = await session.scalar(
        select(Route.id).where(Route.id == route_id, Route.is_active.is_(True))
    )
    if route_exists is None:
        return None
    session.add(DriverRoute(driver_telegram_id=driver_telegram_id, route_id=route_id))
    await session.flush()
    return True


async def active_driver_orders(
    session,
    driver_telegram_id: int,
    limit: int = 20,
    trip_type: str | None = None,
    vip_only: bool = False,
) -> list[Order]:
    """Fetch pending orders matching this working driver's subscription and route choices."""
    statement = (
        select(Order)
        .options(joinedload(Order.route), joinedload(Order.passenger))
        .join(DriverRoute, DriverRoute.route_id == Order.route_id)
        .join(User, User.telegram_id == DriverRoute.driver_telegram_id)
        .join(DriverSubscription, DriverSubscription.driver_id == User.telegram_id)
        .where(
            Order.status == "pending",
            DriverRoute.driver_telegram_id == driver_telegram_id,
            User.role == "driver",
            User.is_working.is_(True),
            DriverSubscription.expires_at > func.now(),
        )
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    if trip_type:
        statement = statement.where(Order.trip_type == trip_type)
    if vip_only:
        statement = statement.where(
            DriverSubscription.vip_expires_at > func.now(),
            (Order.whole_car.is_(True)) | (Order.seats >= 4),
        )
    return list((await session.scalars(statement)).all())


async def add_driver_order_notification(
    session, order_id: int, driver_telegram_id: int, chat_id: int, message_id: int
) -> bool:
    """Save a driver's private order-card reference only while the order remains pending."""
    status = await session.scalar(
        select(Order.status).where(Order.id == order_id).with_for_update()
    )
    if status != "pending":
        return False
    session.add(DriverOrderNotification(
        order_id=order_id,
        driver_telegram_id=driver_telegram_id,
        chat_id=chat_id,
        message_id=message_id,
    ))
    await session.flush()
    return True


async def driver_order_notifications(session, order_id: int) -> list[DriverOrderNotification]:
    """Return all private driver-message references for one order."""
    result = await session.scalars(
        select(DriverOrderNotification).where(DriverOrderNotification.order_id == order_id)
    )
    return list(result.all())


async def ensure_default_routes(session) -> None:
    """Create the built-in route set once during startup without per-request writes."""
    values = [{"origin": origin, "destination": destination, "base_price": 0, "is_active": True} for origin, destination in DEFAULT_ROUTES]
    statement = insert(Route).values(values).on_conflict_do_nothing(constraint="uq_route_origin_destination")
    await session.execute(statement)
    await session.execute(
        update(Route)
        .where(Route.origin == "Ош", Route.destination == "Джалал-Абад")
        .values(is_active=False)
    )


async def active_routes(session) -> list[Route]:
    """Return active routes in stable display order using one read query."""
    result = await session.scalars(select(Route).where(Route.is_active.is_(True)).order_by(Route.id))
    return list(result.all())


async def get_or_create_custom_route(session, origin: str, destination: str) -> Route:
    """Return a matching route or create a bounded, inactive custom route."""
    origin, destination = origin.strip(), destination.strip()
    if not origin or not destination or len(origin) > 100 or len(destination) > 100:
        raise ValueError("Route names must contain 1 to 100 characters")
    route = await session.scalar(
        select(Route).where(
            func.lower(Route.origin) == origin.lower(),
            func.lower(Route.destination) == destination.lower(),
        )
    )
    if route is None:
        route = Route(origin=origin, destination=destination, base_price=0, is_active=False)
        session.add(route)
        await session.flush()
    return route


async def other_routes(session) -> list[Route]:
    """Return active routes omitted from the compact popular-route menu."""
    popular_pairs = {
        ("бишкек", "ош"), ("бишкек", "каракол"),
        ("бишкек", "балыкчы"), ("бишкек", "чолпон-ата"),
    }
    routes = await active_routes(session)
    return [route for route in routes if (route.origin.casefold(), route.destination.casefold()) not in popular_pairs]


async def popular_driver_routes(session, driver_telegram_id: int) -> list[Route]:
    """Return the four fixed driver routes plus any custom routes already saved by this driver."""
    popular_pairs = {
        ("бишкек", "ош"), ("ош", "бишкек"),
        ("бишкек", "каракол"), ("каракол", "бишкек"),
    }
    selected_custom_result = await session.scalars(
        select(Route)
        .join(DriverRoute, DriverRoute.route_id == Route.id)
        .where(
            DriverRoute.driver_telegram_id == driver_telegram_id,
            DriverRoute.is_custom.is_(True),
        )
        .order_by(Route.origin, Route.destination)
    )
    selected_custom = list(selected_custom_result.all())
    available = await session.scalars(
        select(Route)
        .where(Route.is_active.is_(True))
        .order_by(Route.id)
    )
    popular = [
        route for route in available
        if (route.origin.casefold(), route.destination.casefold()) in popular_pairs
    ]
    by_id = {route.id: route for route in (*popular, *selected_custom)}
    return list(by_id.values())


async def add_driver_custom_route(session, driver_telegram_id: int, route_id: int) -> bool:
    """Attach a custom route to a driver without enabling it globally for passenger orders."""
    existing = await session.execute(
        update(DriverRoute)
        .where(
            DriverRoute.driver_telegram_id == driver_telegram_id,
            DriverRoute.route_id == route_id,
        )
        .values(is_custom=True)
    )
    if existing.rowcount:
        return False
    session.add(DriverRoute(
        driver_telegram_id=driver_telegram_id,
        route_id=route_id,
        is_custom=True,
    ))
    await session.flush()
    return True


async def make_order(session, passenger, route_id: int, departure_at: datetime, seats: int, whole_car: bool, trip_type: str, phone: str, departure_period: str = "exact") -> Order:
    """Create an order in the current transaction after validating persisted references."""
    if seats < 1 or seats > 6 or trip_type not in {"passenger", "parcel"}:
        raise ValueError("Invalid order options")
    if len(phone) > 32:
        raise ValueError("Phone number is too long")
    order = Order(
        passenger_id=passenger.id,
        route_id=route_id,
        departure_at=departure_at,
        departure_period=departure_period,
        seats=seats,
        whole_car=whole_car,
        trip_type=trip_type,
        phone=phone,
    )
    session.add(order)
    await session.flush()
    return order


async def set_driver_message_reference(session, order_id: int, chat_id: int, message_id: int) -> bool:
    """Persist the drivers-chat card reference unless the passenger already cancelled."""
    result = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status != "cancelled")
        .values(driver_chat_id=chat_id, driver_message_id=message_id)
    )
    return result.rowcount == 1


async def cancel_passenger_order(
    session, order_id: int, telegram_id: int
) -> tuple[str | None, int | None, int | None, bool]:
    """Atomically cancel a passenger-owned pending order and return its driver-card reference."""
    passenger_id = select(User.id).where(User.telegram_id == telegram_id).scalar_subquery()
    result = await session.execute(
        update(Order)
        .where(
            Order.id == order_id,
            Order.passenger_id == passenger_id,
            Order.status == "pending",
        )
        .values(status="cancelled")
        .returning(Order.status, Order.driver_chat_id, Order.driver_message_id)
    )
    updated = result.one_or_none()
    if updated:
        return updated.status, updated.driver_chat_id, updated.driver_message_id, True

    existing = await session.execute(
        select(Order.status, Order.driver_chat_id, Order.driver_message_id)
        .join(User, User.id == Order.passenger_id)
        .where(Order.id == order_id, User.telegram_id == telegram_id)
    )
    row = existing.one_or_none()
    if row is None:
        return None, None, None, False
    return row.status, row.driver_chat_id, row.driver_message_id, False


async def order_with_details(session, order_id: int) -> Order | None:
    """Load an order and its related route and users without later lazy queries."""
    statement = select(Order).options(
        joinedload(Order.route),
        joinedload(Order.passenger),
        joinedload(Order.driver),
    ).where(Order.id == order_id)
    return await session.scalar(statement)


async def accept_order(session, order_id: int, driver: User) -> bool:
    """Atomically assign a pending order to one driver; return whether it won the race."""
    result = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == "pending")
        .values(status="accepted", driver_id=driver.id)
    )
    return result.rowcount == 1


async def passenger_orders(session, passenger_id: int) -> list[Order]:
    """Fetch only orders still waiting for a driver, with route data preloaded."""
    statement = (
        select(Order)
        .options(joinedload(Order.route))
        .where(
            Order.passenger_id == passenger_id,
            Order.status == "pending",
        )
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    return list((await session.scalars(statement)).all())


async def admin_stats(session) -> tuple[int, int, int]:
    """Calculate user, active-order, and completed-order totals in one database round trip."""
    statement = select(
        select(func.count(User.id)).scalar_subquery(),
        func.count(case((Order.status.in_(["pending", "accepted"]), 1))).label("active_orders"),
        func.count(case((Order.status == "completed", 1))).label("completed_orders"),
    ).select_from(Order)
    result = (await session.execute(statement)).one()
    return int(result[0] or 0), int(result[1] or 0), int(result[2] or 0)


async def user_id_batches(session, batch_size: int = 500) -> AsyncIterator[list[int]]:
    """Yield Telegram IDs in keyset-paginated batches to bound broadcast memory use."""
    last_id = 0
    while True:
        statement = (
            select(User.telegram_id)
            .where(User.telegram_id > last_id)
            .order_by(User.telegram_id)
            .limit(batch_size)
        )
        batch = list((await session.scalars(statement)).all())
        if not batch:
            break
        yield batch
        last_id = batch[-1]


async def set_route_price(session, route_id: int, price: int) -> bool:
    """Set a nonnegative route price and report whether the route exists."""
    if price < 0:
        return False
    result = await session.execute(
        update(Route).where(Route.id == route_id).values(base_price=price)
    )
    return result.rowcount == 1


async def create_route(session, origin: str, destination: str, price: int) -> Route:
    """Create a route after enforcing database field bounds and a nonnegative price."""
    origin, destination = origin.strip(), destination.strip()
    if not origin or not destination or len(origin) > 100 or len(destination) > 100 or price < 0:
        raise ValueError("Invalid route fields")
    route = Route(origin=origin, destination=destination, base_price=price)
    session.add(route)
    await session.flush()
    return route


async def deactivate_route(session, route_id: int) -> bool:
    """Hide a route from passengers without deleting historical order references."""
    result = await session.execute(
        update(Route).where(Route.id == route_id).values(is_active=False)
    )
    return result.rowcount == 1
