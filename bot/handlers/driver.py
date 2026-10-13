"""Driver menus, availability, routes, order views, subscriptions, and referrals."""
import logging
import re
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from bot.config import settings
from bot.database.models import DriverRoute, DriverSubscription, Order, User
from bot.database.requests import (
    active_driver_orders,
    add_driver_custom_route,
    get_or_create_custom_route,
    get_driver_routes,
    popular_driver_routes,
    toggle_driver_route,
)
from bot.i18n import tr
from bot.keyboards.inline import (
    DriverRouteCallback,
    driver_order_keyboard,
    driver_routes_keyboard,
    subscription_keyboard,
    vip_subscription_keyboard,
)
from bot.keyboards.reply import driver_menu
from bot.services.payment import create_payment_request, create_qr_png
from bot.services.subscription import (
    subscription_expiry,
    subscription_is_active,
    vip_subscription_expiry,
    vip_subscription_is_active,
)
from bot.states.driver_state import DriverState

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
logger = logging.getLogger(__name__)


async def _get_driver(session, telegram_id: int) -> User | None:
    """Load an account only if its persisted role is driver."""
    return await session.scalar(
        select(User).where(User.telegram_id == telegram_id, User.role == "driver")
    )


async def _show_subscription_prompt(message: Message, language: str) -> None:
    """Display the available subscription plans using configured prices."""
    keyboard = subscription_keyboard(
        language,
        settings.subscription_price_7_days,
        settings.subscription_price_30_days,
        settings.payment_currency,
    )
    await message.answer(tr(language, "subscription_plans"), reply_markup=keyboard)


async def _send_filtered_orders(message: Message, session, driver: User, *, trip_type=None, vip_only=False) -> None:
    """Show matching pending orders for a working driver with an active subscription."""
    language = driver.language or "ru"
    if not await subscription_is_active(session, driver.telegram_id):
        await message.answer(tr(language, "driver_subscription_expired"), reply_markup=driver_menu(language, driver.is_working))
        await _show_subscription_prompt(message, language)
        return
    if not driver.is_working:
        await message.answer(tr(language, "driver_pause_done"), reply_markup=driver_menu(language, False))
        return
    orders = await active_driver_orders(
        session, driver.telegram_id, trip_type=trip_type, vip_only=vip_only
    )
    if not orders:
        await message.answer(tr(language, "driver_no_filtered_orders"), reply_markup=driver_menu(language, driver.is_working))
        return
    zone = ZoneInfo(settings.timezone)
    for order in orders:
        route = f"{order.route.origin} ➔ {order.route.destination}"
        seats = tr(language, "driver_whole_car") if order.whole_car else str(order.seats)
        departure = order.departure_at.astimezone(zone).strftime("%d.%m.%Y %H:%M")
        category = (
            tr(language, "driver_parcel_type") if order.trip_type == "parcel"
            else tr(language, "driver_vip_type") if (order.whole_car or order.seats >= 4)
            else tr(language, "driver_passenger_type")
        )
        passenger_label = (
            f"@{order.passenger.username}"
            if order.passenger.username
            else order.passenger.full_name
        )
        text = (
            f"🆕 Заказ №{order.id}\n"
            f"{tr(language, 'driver_route')}: {route}\n"
            f"{tr(language, 'driver_departure')}: {departure}\n"
            f"{tr(language, 'driver_seats')}: {seats}\n"
            f"{tr(language, 'driver_type')}: {category}\n"
            f"{tr(language, 'driver_phone')}: {order.phone or tr(language, 'phone_missing')}\n"
            f"{tr(language, 'driver_passenger')}: {passenger_label}"
        )
        await message.answer(
            text,
            reply_markup=driver_order_keyboard(
                order.phone,
                order.id,
                language,
                passenger_id=order.passenger.telegram_id,
            ),
        )


@router.message(F.text.in_({tr("ru", "driver_pause"), tr("ky", "driver_pause"), tr("ru", "driver_resume"), tr("ky", "driver_resume")}))
async def toggle_work_status(message: Message, session) -> None:
    """Pause or resume order delivery and persist the driver's work state."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    new_status = not driver.is_working
    await session.execute(
        update(User).where(User.telegram_id == driver.telegram_id).values(is_working=new_status)
    )
    await session.commit()
    language = driver.language or "ru"
    driver.is_working = new_status
    response = tr(language, "driver_resume_done" if new_status else "driver_pause_done")
    await message.answer(response, reply_markup=driver_menu(language, new_status))


@router.message(F.text.in_({tr("ru", "driver_extend"), tr("ky", "driver_extend"), tr("ru", "driver_subscription"), tr("ky", "driver_subscription")}))
async def show_driver_subscription(message: Message, session) -> None:
    """Show current subscription information and a shortcut to purchase more time."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    language = driver.language or "ru"
    expires_at = await subscription_expiry(session, driver.telegram_id)
    if expires_at and await subscription_is_active(session, driver.telegram_id):
        shown_expiry = expires_at.astimezone(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y %H:%M")
        await message.answer(
            tr(language, "driver_subscription_active", expires_at=shown_expiry),
            reply_markup=driver_menu(language, driver.is_working),
        )
    else:
        await message.answer(
            tr(language, "driver_subscription_expired"),
            reply_markup=driver_menu(language, driver.is_working),
        )
    await _show_subscription_prompt(message, language)


@router.message(F.text.in_({tr("ru", "driver_status"), tr("ky", "driver_status")}))
async def show_driver_status(message: Message, session) -> None:
    """Report subscription expiry, availability, and selected route preferences."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    language = driver.language or "ru"
    expires_at = await subscription_expiry(session, driver.telegram_id)
    active = bool(expires_at and await subscription_is_active(session, driver.telegram_id))
    shown_expiry = expires_at.astimezone(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y %H:%M") if expires_at else "—"
    route_rows = await get_driver_routes(session, driver.telegram_id)
    routes = ", ".join(f"{route.origin} ➔ {route.destination}" for route in route_rows)
    await message.answer(
        tr(
            language,
            "driver_status_text",
            subscription=tr(language, "driver_subscription_status_active" if active else "driver_subscription_status_expired"),
            expires_at=shown_expiry,
            working=tr(language, "driver_working" if driver.is_working else "driver_paused"),
            routes=routes or tr(language, "driver_routes_empty"),
        ),
        reply_markup=driver_menu(language, driver.is_working),
    )


@router.message(F.text.in_({tr("ru", "driver_routes"), tr("ky", "driver_routes")}))
async def show_driver_routes(message: Message, session) -> None:
    """Display route checkboxes for drivers to choose where they receive orders."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    routes = await popular_driver_routes(session, driver.telegram_id)
    selected = {route.id for route in await get_driver_routes(session, driver.telegram_id)}
    language = driver.language or "ru"
    await message.answer(
        tr(language, "driver_route_prompt"),
        reply_markup=driver_routes_keyboard(routes, selected, language),
    )


@router.callback_query(F.data == "add_custom_route")
async def begin_custom_route(callback: CallbackQuery, state: FSMContext, session) -> None:
    """Start the driver-only custom route input flow."""
    driver = await _get_driver(session, callback.from_user.id)
    if driver is None or callback.message is None:
        await callback.answer(tr("ru", "driver_access_denied"), show_alert=True)
        return
    await state.set_state(DriverState.waiting_custom_route)
    await callback.message.answer(
        tr(driver.language, "driver_custom_route_prompt"),
        reply_markup=driver_menu(driver.language or "ru", driver.is_working),
    )
    await callback.answer()


@router.message(DriverState.waiting_custom_route, F.text)
async def save_custom_route(message: Message, state: FSMContext, session) -> None:
    """Validate and save a driver's custom route preference."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await state.clear()
        await message.answer(tr("ru", "driver_access_denied"))
        return
    raw = message.text.strip()
    parts = re.split(r"\s*(?:->|—|–|-)\s*", raw, maxsplit=1)
    if len(parts) != 2:
        await message.answer(tr(driver.language, "driver_custom_route_invalid"))
        return
    origin, destination = (part.strip() for part in parts)
    if (
        not origin or not destination or origin.casefold() == destination.casefold()
        or len(origin) > 100 or len(destination) > 100
        or not re.fullmatch(r"[\w .’'()\-]{1,100}", origin, re.UNICODE)
        or not re.fullmatch(r"[\w .’'()\-]{1,100}", destination, re.UNICODE)
    ):
        await message.answer(tr(driver.language, "driver_custom_route_invalid"))
        return
    try:
        route = await get_or_create_custom_route(session, origin, destination)
        await add_driver_custom_route(session, driver.telegram_id, route.id)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("Could not save custom route for driver %s", driver.telegram_id)
        await message.answer(tr(driver.language, "payment_error"))
        return
    await state.clear()
    language = driver.language or "ru"
    await message.answer(
        tr(language, "driver_custom_route_added", route=f"{route.origin} ➔ {route.destination}"),
        reply_markup=driver_menu(language, driver.is_working),
    )
    routes = await popular_driver_routes(session, driver.telegram_id)
    selected = {item.id for item in await get_driver_routes(session, driver.telegram_id)}
    await message.answer(
        tr(language, "driver_route_prompt"),
        reply_markup=driver_routes_keyboard(routes, selected, language),
    )


@router.callback_query(DriverRouteCallback.filter())
async def toggle_route(callback: CallbackQuery, callback_data: DriverRouteCallback, session) -> None:
    """Persist a route checkbox change and refresh the inline selector."""
    driver = await _get_driver(session, callback.from_user.id)
    if driver is None:
        await callback.answer(tr("ru", "driver_access_denied"), show_alert=True)
        return
    result = await toggle_driver_route(session, driver.telegram_id, callback_data.route_id)
    if result is None:
        await callback.answer(tr(driver.language, "driver_route_unavailable"), show_alert=True)
        return
    await session.commit()
    routes = await popular_driver_routes(session, driver.telegram_id)
    selected = {route.id for route in await get_driver_routes(session, driver.telegram_id)}
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=driver_routes_keyboard(routes, selected, driver.language or "ru")
        )
    await callback.answer(tr(driver.language, "driver_route_saved"))


@router.message(F.text.in_({tr("ru", "driver_parcels"), tr("ky", "driver_parcels")}))
async def show_driver_parcels(message: Message, session) -> None:
    """Show active parcel orders for a driver whose subscription and route match."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    await _send_filtered_orders(message, session, driver, trip_type="parcel")


@router.message(F.text.in_({tr("ru", "driver_vip"), tr("ky", "driver_vip")}))
async def show_driver_vip_orders(message: Message, session) -> None:
    """Show VIP status and matching orders, or offer VIP subscription plans."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    language = driver.language or "ru"
    expiry = await vip_subscription_expiry(session, driver.telegram_id)
    if not expiry or not await vip_subscription_is_active(session, driver.telegram_id):
        await message.answer(
            tr(language, "driver_vip_prompt"),
            reply_markup=vip_subscription_keyboard(
                language,
                settings.subscription_price_7_days,
                settings.subscription_price_30_days,
                settings.payment_currency,
            ),
        )
        return
    shown_expiry = expiry.astimezone(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y %H:%M")
    await message.answer(tr(language, "driver_vip_active", expires_at=shown_expiry))
    await _send_filtered_orders(message, session, driver, vip_only=True)


@router.message(F.text.in_({tr("ru", "driver_contact"), tr("ky", "driver_contact")}))
async def contact_support(message: Message, session) -> None:
    """Show the configured support account to a driver."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    username = settings.support_username.lstrip("@")
    await message.answer(tr(driver.language, "support", contact=f"@{username}"))


@router.message(F.text.in_({tr("ru", "driver_referral"), tr("ky", "driver_referral")}))
async def show_driver_referral(message: Message, session) -> None:
    """Generate a deep link that attributes a new driver to this inviter."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    bot_info = await message.bot.get_me()
    url = f"https://t.me/{bot_info.username}?start=driverref_{driver.telegram_id}"
    await message.answer(tr(driver.language, "driver_referral_text", url=url), reply_markup=driver_menu(driver.language or "ru", driver.is_working))


@router.message(F.text.in_({tr("ru", "driver_orders"), tr("ky", "driver_orders")}))
async def show_driver_orders(message: Message, session) -> None:
    """Show pending orders matching this driver's active status and selected routes."""
    driver = await _get_driver(session, message.from_user.id)
    if driver is None:
        await message.answer(tr("ru", "driver_access_denied"))
        return
    await _send_filtered_orders(message, session, driver)


@router.callback_query(F.data.startswith("hide_order:"))
async def hide_order(callback: CallbackQuery, session) -> None:
    """Delete this driver's own order notification without changing the order."""
    driver = await _get_driver(session, callback.from_user.id)
    if driver is None or callback.message is None:
        await callback.answer(tr("ru", "driver_access_denied"), show_alert=True)
        return
    try:
        order_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(tr("ru", "stale_keyboard"), show_alert=True)
        return
    try:
        await callback.message.delete()
    except TelegramAPIError:
        logger.exception("Could not hide order %s from driver %s", order_id, driver.telegram_id)
        await callback.answer(tr(driver.language, "driver_hidden"), show_alert=True)
        return
    await callback.answer(tr(driver.language, "driver_hidden"))


@router.callback_query(F.data.startswith("call_order:"))
async def send_order_contact(callback: CallbackQuery, session) -> None:
    """Send the passenger's phone as a Telegram contact to the requesting driver."""
    driver = await _get_driver(session, callback.from_user.id)
    if driver is None:
        await callback.answer(tr("ru", "driver_access_denied"), show_alert=True)
        return
    try:
        order_id = int(callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await callback.answer(tr("ru", "stale_keyboard"), show_alert=True)
        return
    order = await session.scalar(
        select(Order)
        .join(DriverRoute, DriverRoute.route_id == Order.route_id)
        .join(User, User.telegram_id == DriverRoute.driver_telegram_id)
        .join(DriverSubscription, DriverSubscription.driver_id == DriverRoute.driver_telegram_id)
        .where(
            Order.id == order_id,
            Order.status == "pending",
            DriverRoute.driver_telegram_id == driver.telegram_id,
            User.role == "driver",
            DriverSubscription.expires_at > func.now(),
            or_(
                (~Order.whole_car.is_(True) & (Order.seats < 4)),
                DriverSubscription.vip_expires_at > func.now(),
            ),
        )
    )
    if order is None:
        await callback.answer(tr(driver.language, "stale_keyboard"), show_alert=True)
        return
    if not order.phone:
        await callback.answer(tr(driver.language, "phone_missing"), show_alert=True)
        return
    try:
        for phone_number in order.phone.split(", "):
            await callback.bot.send_contact(
                chat_id=driver.telegram_id,
                phone_number=phone_number,
                first_name=tr(driver.language, "driver_passenger"),
            )
    except TelegramAPIError:
        logger.exception("Could not send passenger contact for order %s to driver %s", order_id, driver.telegram_id)
        await callback.answer(tr(driver.language, "payment_error"), show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("subpay:"))
async def create_subscription_payment(callback: CallbackQuery, session) -> None:
    """Create a pending plan transaction and return its QR code to the driver."""
    await _create_payment(callback, session, subscription_type="regular")


@router.callback_query(F.data.startswith("vippay:"))
async def create_vip_payment(callback: CallbackQuery, session) -> None:
    """Create a separate VIP payment transaction and deliver its QR code."""
    await _create_payment(callback, session, subscription_type="vip")


async def _create_payment(callback: CallbackQuery, session, *, subscription_type: str) -> None:
    """Create and deliver a pending regular or VIP subscription payment."""
    driver = await _get_driver(session, callback.from_user.id)
    if driver is None:
        await callback.answer(tr("ru", "driver_access_denied"), show_alert=True)
        return
    language = driver.language or "ru"
    try:
        duration_days = int(callback.data.rsplit(":", 1)[1])
        if duration_days not in {7, 30}:
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer(tr(language, "stale_keyboard"), show_alert=True)
        return
    amount = settings.subscription_price_7_days if duration_days == 7 else settings.subscription_price_30_days
    try:
        payment = await create_payment_request(
            session, driver.telegram_id, duration_days, amount,
            subscription_type=subscription_type,
        )
        await session.commit()
    except (ValueError, SQLAlchemyError):
        await session.rollback()
        logger.exception("Could not create subscription payment transaction")
        await callback.answer(tr(language, "payment_error"), show_alert=True)
        return
    caption = tr(language, "payment_qr_caption", transaction_id=payment.external_tx_id, url=payment.checkout_url)
    await callback.answer(tr(language, "payment_created"))
    try:
        qr_bytes = create_qr_png(payment.checkout_url)
        await callback.message.answer_photo(
            BufferedInputFile(qr_bytes, filename="subscription-payment.png"),
            caption=caption,
            reply_markup=driver_menu(language, driver.is_working),
        )
    except (ValueError, TelegramAPIError):
        logger.exception("Could not deliver subscription payment QR")
        await callback.message.answer(caption, reply_markup=driver_menu(language, driver.is_working))
