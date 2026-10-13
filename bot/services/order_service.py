"""Route-aware delivery of passenger orders to eligible subscribed drivers."""
import logging
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload

from bot.config import settings
from bot.database.engine import SessionFactory
from bot.database.models import DriverRoute, DriverSubscription, Order, User
from bot.database.requests import add_driver_order_notification, order_with_details
from bot.i18n import tr
from bot.keyboards.inline import driver_order_keyboard

logger = logging.getLogger(__name__)


async def send_order_to_active_drivers(bot: Bot, order_id: int) -> int:
    """Send a committed order to working, subscribed drivers who selected its route."""
    async with SessionFactory() as session:
        order = await order_with_details(session, order_id)
        if order is None or order.status != "pending":
            logger.info("Skip driver fan-out: order %s is missing or no longer pending", order_id)
            return 0
        driver_query = (
            select(User.telegram_id, User.language)
            .join(DriverSubscription, DriverSubscription.driver_id == User.telegram_id)
            .join(DriverRoute, DriverRoute.driver_telegram_id == User.telegram_id)
            .where(
                User.role == "driver",
                User.is_working.is_(True),
                DriverSubscription.expires_at > func.now(),
                DriverRoute.route_id == order.route_id,
            )
            .distinct()
            .order_by(User.telegram_id)
        )
        vip_order = bool(order.whole_car or order.seats >= 4)
        if vip_order:
            driver_query = driver_query.where(DriverSubscription.vip_expires_at > func.now())
        drivers = list((await session.execute(driver_query)).all())
        if not drivers:
            logger.info("No active working drivers selected route %s for order %s", order.route_id, order_id)
            return 0
        route_name = f"{order.route.origin} ➔ {order.route.destination}"
        departure = order.departure_at.astimezone(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y %H:%M")
        client_name = order.passenger.full_name or str(order.passenger.telegram_id)

    sent_count = 0
    for driver_id, driver_language in drivers:
        language = driver_language or "ru"
        seats = tr(language, "driver_whole_car") if order.whole_car else str(order.seats)
        category = (
            tr(language, "driver_parcel_type") if order.trip_type == "parcel"
            else tr(language, "driver_vip_type") if vip_order
            else tr(language, "driver_passenger_type")
        )
        passenger_label = f"@{order.passenger.username}" if order.passenger.username else client_name
        message_text = (
            f"🆕 Заказ №{order.id}\n"
            f"{tr(language, 'driver_route')}: {route_name}\n"
            f"{tr(language, 'driver_departure')}: {departure}\n"
            f"{tr(language, 'driver_seats')}: {seats}\n"
            f"{tr(language, 'driver_type')}: {category}\n"
            f"{tr(language, 'driver_phone')}: {order.phone or tr(language, 'phone_missing')}\n"
            f"{tr(language, 'driver_passenger')}: {passenger_label}"
        )
        try:
            sent = await bot.send_message(
                chat_id=driver_id,
                text=message_text,
                reply_markup=driver_order_keyboard(
                    order.phone,
                    order.id,
                    language,
                    passenger_id=order.passenger.telegram_id,
                ),
            )
        except TelegramForbiddenError:
            logger.warning("Driver %s blocked the bot; order %s was not delivered", driver_id, order_id)
            continue
        except TelegramAPIError:
            logger.exception("Telegram rejected order %s for driver %s", order_id, driver_id)
            continue

        try:
            async with SessionFactory() as session:
                saved = await add_driver_order_notification(
                    session, order.id, driver_id, driver_id, sent.message_id
                )
                await session.commit()
            if saved:
                sent_count += 1
            else:
                await bot.edit_message_reply_markup(
                    chat_id=driver_id,
                    message_id=sent.message_id,
                    reply_markup=None,
                )
        except SQLAlchemyError:
            logger.exception("Could not store Telegram reference for order %s / driver %s", order_id, driver_id)
        except TelegramAPIError:
            logger.exception("Could not remove stale order card %s from driver %s", order_id, driver_id)
    return sent_count
