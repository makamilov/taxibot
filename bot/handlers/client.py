"""Passenger-specific sharing, completion, and quick return-trip handlers."""
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import re

from aiogram import F, Router
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.enums import ParseMode
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

from bot.config import settings
from bot.database.models import Order
from bot.database.requests import active_routes, get_or_create_custom_route, get_user, upsert_user
from bot.handlers.order import (
    ask_smart_order_text,
    start_smart_order,
    dismiss_inline,
    my_orders,
    prompt_for_departure_date,
    reject_stale_order_callback,
    router as order_router,
    send_inline_prompt,
)
from bot.i18n import tr
from bot.keyboards.inline import (
    FoundCarCallback,
    ReturnOrderCallback,
    found_car_keyboard,
    invite_friend_keyboard,
)
from bot.keyboards.reply import main_menu
from bot.services.driver_notifications import notify_active_drivers
from bot.states.order_state import OrderFlow

router = Router()


class ClientOrderLanguageMiddleware(BaseMiddleware):
    """Refresh the active client order's language from PostgreSQL on each update."""

    async def __call__(self, handler, event, data):
        state = data.get("state")
        session = data.get("session")
        telegram_user = getattr(event, "from_user", None)
        current_state = await state.get_state() if state else None
        if (
            current_state
            and current_state.startswith("OrderFlow:")
            and session is not None
            and telegram_user is not None
        ):
            user = await get_user(session, telegram_user.id)
            if user is not None and user.role == "client":
                await state.update_data(language=user.language_code or "ru")
        return await handler(event, data)


router.message.outer_middleware(ClientOrderLanguageMiddleware())
router.callback_query.outer_middleware(ClientOrderLanguageMiddleware())


async def _show_departure_prompt(message: Message, state: FSMContext, route_name: str, language: str) -> None:
    """Replace the route prompt with a localized date prompt and remove stale reply buttons."""
    await prompt_for_departure_date(message, state, route_name, language)


@router.callback_query(OrderFlow.route, F.data.regexp(r"^route:\d+$"))
@router.callback_query(OrderFlow.more_routes, F.data.regexp(r"^route:\d+$"))
async def choose_client_route(callback: CallbackQuery, state: FSMContext, session) -> None:
    """Select a route and replace the stale reply keyboard with the date-step interface."""
    if await reject_stale_order_callback(callback, state):
        return
    user = await get_user(session, callback.from_user.id)
    if user is None or user.role != "client" or callback.message is None:
        language = user.language_code if user else "ru"
        await callback.answer(tr(language, "client_only"), show_alert=True)
        return
    try:
        route_id = int(callback.data.split(":", 1)[1])
    except (AttributeError, ValueError):
        await callback.answer(tr(user.language_code, "route_unavailable"), show_alert=True)
        return
    routes = await active_routes(session)
    route = next((item for item in routes if item.id == route_id), None)
    if route is None:
        await callback.answer(tr(user.language_code, "route_unavailable"), show_alert=True)
        return
    language = user.language_code or "ru"
    route_name = f"{route.origin} — {route.destination}"
    await state.update_data(language=language, route_id=route.id, route=route_name)
    await state.set_state(OrderFlow.departure)
    await dismiss_inline(callback)
    await _show_departure_prompt(callback.message, state, route_name, language)
    await callback.answer()


@router.message(F.text.in_({tr("ru", "menu_invite"), tr("ky", "menu_invite"), "📩 Досту чакыруу"}))
async def invite_friend(message: Message, session) -> None:
    """Send the localized share prompt with a personal bot referral link."""
    user = await get_user(session, message.from_user.id)
    if user is None or user.role != "client":
        await message.answer(tr(user.language_code if user else "ru", "client_only"))
        return
    bot_info = await message.bot.get_me()
    await message.answer(
        tr(user.language, "invite_text"),
        reply_markup=invite_friend_keyboard(bot_info.username, user.telegram_id, user.language_code or "ru"),
    )


@router.message(F.text == "🚗 Автоунаа табуу")
async def begin_order_from_kyrgyz_menu(
    message: Message,
    state: FSMContext,
    session,
) -> None:
    """Route the localized Kyrgyz client menu action into the existing order flow."""
    user = await get_user(session, message.from_user.id)
    if user is None:
        user = await upsert_user(session, message.from_user, language="ky")
    if user.role != "client":
        return UNHANDLED
    language = user.language_code or "ky"
    await start_smart_order(message, state, language)


@router.message(F.text == "📋 Менин сапарларым")
async def show_client_orders_from_kyrgyz_menu(message: Message, session) -> None:
    """Route the localized Kyrgyz trips button into the active-order handler."""
    user = await get_user(session, message.from_user.id)
    if user is None or user.role != "client":
        return UNHANDLED
    await my_orders(message, session)


@router.message(F.text.in_({"☎️ Байланышуу", "ℹ️ Жардам"}))
async def client_support_from_kyrgyz_menu(message: Message, session) -> None:
    """Serve the Kyrgyz contact and help buttons without changing shared handlers."""
    user = await get_user(session, message.from_user.id)
    if user is None or user.role != "client":
        return UNHANDLED
    language = user.language_code or "ky"
    keyboard = main_menu(language)
    if message.text == "☎️ Байланышуу":
        contact = (
            f"@{settings.support_username.lstrip('@')}"
            if settings.support_username
            else tr(language, "support_missing")
        )
        await message.answer(tr(language, "support", contact=contact), reply_markup=keyboard)
    else:
        await message.answer(tr(language, "about"), reply_markup=keyboard)


@router.callback_query(OrderFlow.more_routes, F.data == "route:custom")
async def begin_client_custom_route(callback: CallbackQuery, state: FSMContext, session) -> None:
    """Explicitly enter the route-input state before prompting the client for text."""
    user = await get_user(session, callback.from_user.id)
    if user is None or user.role != "client":
        return UNHANDLED
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    data = await state.get_data()
    language = user.language_code or data.get("language") or "ru"
    await ask_smart_order_text(callback.message, state, language)
    await callback.answer()


@router.message(OrderFlow.custom_route, F.text)
async def save_client_custom_route(message: Message, state: FSMContext, session) -> None:
    """Save a manually entered client route and advance directly to departure date."""
    user = await get_user(session, message.from_user.id)
    if user is None or user.role != "client":
        return UNHANDLED
    route_text = message.text
    parts = re.split(r"\s*(?:->|—|–|-)\s*", route_text.strip(), maxsplit=1)
    if len(parts) != 2:
        data = await state.get_data()
        language = data.get("language") or user.language or "ru"
        await send_inline_prompt(message, state, tr(language, "custom_route_invalid"), None)
        return
    origin, destination = (part.strip() for part in parts)
    if not origin or not destination or max(len(origin), len(destination)) > 100:
        data = await state.get_data()
        language = data.get("language") or user.language or "ru"
        await send_inline_prompt(message, state, tr(language, "custom_route_invalid"), None)
        return
    route = await get_or_create_custom_route(session, origin, destination)
    data = await state.get_data()
    language = user.language_code or data.get("language") or "ru"
    await state.update_data(route=route_text, route_id=route.id)
    await state.set_state(OrderFlow.departure)
    await _show_departure_prompt(
        message,
        state,
        f"{route.origin} — {route.destination}",
        language,
    )


@router.callback_query(FoundCarCallback.filter())
async def found_car(
    callback: CallbackQuery,
    callback_data: FoundCarCallback,
    session,
) -> None:
    """Complete the passenger's own active order and replace its card with confirmation."""
    if callback_data.action != "search":
        await callback.answer(tr("ru", "order_completed"), show_alert=True)
        return
    user = await get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer(tr("ru", "client_only"), show_alert=True)
        return
    language = user.language_code or "ru"
    if user.role != "client":
        await callback.answer(tr(language, "client_only"), show_alert=True)
        return

    order = await session.scalar(
        select(Order)
        .options(joinedload(Order.route))
        .where(Order.id == callback_data.order_id, Order.passenger_id == user.id)
        .with_for_update(of=Order)
    )
    if order is None or order.status not in {"pending", "active", "accepted", "in_progress"}:
        await callback.answer(tr(language, "order_completed"), show_alert=True)
        return

    # The row is locked above, so a second tap cannot race this transition.
    order.status = "completed"
    await session.commit()
    if callback.message:
        bot_info = await callback.bot.get_me()
        confirmation = tr(language, "found_car_message")
        invite_markup = invite_friend_keyboard(
            bot_info.username, user.telegram_id, language
        )
        try:
            await callback.message.edit_text(
                confirmation,
                reply_markup=invite_markup,
                parse_mode=ParseMode.HTML,
            )
        except TelegramAPIError:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
            await callback.message.answer(
                confirmation,
                reply_markup=invite_markup,
                parse_mode=ParseMode.HTML,
            )
    await callback.answer()


@router.callback_query(F.data == "return_order_cancel")
async def dismiss_return_offer(callback: CallbackQuery, session) -> None:
    """Close date choices while leaving the friend-invite action available."""
    user = await get_user(session, callback.from_user.id)
    if user is None or user.role != "client":
        await callback.answer(tr(user.language_code if user else "ru", "client_only"), show_alert=True)
        return
    if callback.message:
        bot_info = await callback.bot.get_me()
        await callback.message.edit_reply_markup(
            reply_markup=invite_friend_keyboard(
                bot_info.username, user.telegram_id, user.language_code or "ru"
            )
        )
    await callback.answer()


@router.callback_query(ReturnOrderCallback.filter())
async def create_return_order(
    callback: CallbackQuery,
    callback_data: ReturnOrderCallback,
    session,
) -> None:
    """Create a reverse order using the previous trip's seats and phone number."""
    if callback_data.action != "order":
        await callback.answer(tr("ru", "order_completed"), show_alert=True)
        return
    user = await get_user(session, callback.from_user.id)
    if user is None:
        await callback.answer(tr("ru", "client_only"), show_alert=True)
        return
    language = user.language_code or "ru"
    if user.role != "client":
        await callback.answer(tr(language, "client_only"), show_alert=True)
        return

    original = await session.scalar(
        select(Order)
        .options(joinedload(Order.route))
        .where(
            Order.id == callback_data.order_id,
            Order.passenger_id == user.id,
            Order.status == "completed",
        )
        .with_for_update(of=Order)
    )
    if original is None or callback_data.when not in {"tomorrow", "in-2-days"}:
        await callback.answer(tr(language, "order_completed"), show_alert=True)
        return

    try:
        reverse_route = await get_or_create_custom_route(
            session, original.route.destination, original.route.origin
        )
        zone = ZoneInfo(settings.timezone)
        days_ahead = 1 if callback_data.when == "tomorrow" else 2
        local_date = datetime.now(zone).date() + timedelta(days=days_ahead)
        local_departure = datetime.combine(local_date, time(hour=9), tzinfo=zone)
        departure_at = local_departure.astimezone(UTC)
        return_order = await session.scalar(
            select(Order).where(
                Order.passenger_id == user.id,
                Order.route_id == reverse_route.id,
                Order.departure_at == departure_at,
                Order.phone == original.phone,
                Order.seats == original.seats,
                Order.whole_car == original.whole_car,
                Order.trip_type == original.trip_type,
                Order.status == "pending",
            )
        )
        created_new = return_order is None
        if created_new:
            return_order = Order(
                passenger_id=user.id,
                route_id=reverse_route.id,
                departure_at=departure_at,
                departure_period="morning",
                seats=original.seats,
                whole_car=original.whole_car,
                trip_type=original.trip_type,
                phone=original.phone,
                status="pending",
            )
            session.add(return_order)
            await session.flush()
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    route_text = f"{reverse_route.origin} ➔ {reverse_route.destination}"
    departure_text = local_departure.strftime("%d.%m.%Y 09:00")
    departure_label = "Завтра" if callback_data.when == "tomorrow" else "Через 2 дня"
    if language == "ky":
        departure_label = "Эртең" if callback_data.when == "tomorrow" else "2 күндөн кийин"
    confirmation = tr(
        language,
        "return_created",
        route=route_text,
        departure=f"{departure_label} ({departure_text})",
    )
    seat_text = tr(language, "driver_whole_car") if return_order.whole_car else str(return_order.seats)
    trip_text = tr(
        language,
        "driver_parcel_type" if return_order.trip_type == "parcel" else "driver_passenger_type",
    )
    driver_text = (
        f"🆕 Заказ №{return_order.id}\n"
        f"{tr(language, 'driver_route')}: {route_text}\n"
        f"{tr(language, 'driver_departure')}: {departure_text}\n"
        f"{tr(language, 'driver_seats')}: {seat_text}\n"
        f"{tr(language, 'driver_type')}: {trip_text}\n"
        f"{tr(language, 'driver_phone')}: {return_order.phone}"
    )
    sent_count = 0
    if created_new:
        sent_count = await notify_active_drivers(
            callback.bot, session, return_order.id, driver_text, return_order.phone
        )
    if created_new and sent_count == 0:
        confirmation += f"\n\n{tr(language, 'return_not_sent')}"
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        await callback.message.answer(
            confirmation,
            reply_markup=found_car_keyboard(return_order.id, language),
        )
    await callback.answer()


# Retain the existing passenger order FSM and its menu handlers under the client router.
router.include_router(order_router)
