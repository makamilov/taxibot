from datetime import date, datetime, time, timedelta
import logging
import re
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from bot.config import settings
from bot.database.requests import (
    active_routes, cancel_passenger_order, get_active_drivers_count, get_or_create_custom_route, get_user, make_order,
    order_with_details, passenger_orders, upsert_user,
)
from bot.i18n import tr
from bot.keyboards.inline import (
    cancel_trip_keyboard, confirm_keyboard, choice_keyboard, departure_period_keyboard,
    found_car_keyboard,
    exact_hours_keyboard, departure_date_inline_keyboard, seats_keyboard,
)
from bot.keyboards.reply import cancel_order_keyboard, main_menu, phone_entry_keyboard
from bot.states.order_state import OrderFlow
from bot.services.driver_notifications import (
    synchronize_order_cancellation,
)
from bot.services.order_service import send_order_to_active_drivers

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
logger = logging.getLogger(__name__)

MONTH_NAMES = {
    "январь": 1, "января": 1, "янв": 1, "февраль": 2, "февраля": 2, "фев": 2,
    "март": 3, "марта": 3, "мар": 3, "апрель": 4, "апреля": 4, "апр": 4,
    "май": 5, "мая": 5, "июнь": 6, "июня": 6, "июн": 6, "июль": 7, "июля": 7,
    "июл": 7, "август": 8, "августа": 8, "авг": 8, "сентябрь": 9, "сентября": 9,
    "сен": 9, "сент": 9, "октябрь": 10, "октября": 10, "окт": 10,
    "ноябрь": 11, "ноября": 11, "нояб": 11, "декабрь": 12, "декабря": 12, "дек": 12,
    "январда": 1, "февралда": 2, "мартта": 3, "апрелде": 4, "майда": 5,
    "июнда": 6, "июлда": 7, "августта": 8, "сентябрда": 9, "октябрда": 10,
    "ноябрда": 11, "декабрда": 12,
}
MONTH_PATTERN = "|".join(
    re.escape(name) for name in sorted(MONTH_NAMES, key=len, reverse=True)
)
SMART_DATE_PATTERN = re.compile(
    rf"(?i)(?<!\w)(?:послезавтра|бүрсүгүнү|бүрсүгүн|бугун|бүгүн|эртең|эртен|сегодня|завтра|кечинде|вечером|\d{{1,2}}[./-]\d{{1,2}}(?:[./-]\d{{2,4}})?|\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+\d{{4}})?)(?:\s+(?:в|саат)\s*\d{{1,2}})?(?!\w)"
)
PERIODS = {
    "morning": (6, 12), "lunch": (12, 17), "evening": (17, 22), "night": (22, 6),
}

def user_language(data: dict) -> str:
    """Return the selected language or Russian when no choice is stored yet."""
    return data.get("language", "ru")


def normalize_phone_number(value: str) -> str:
    """Normalize Kyrgyz local numbers to +996 while preserving other country codes."""
    digits = re.sub(r"\D", "", value)
    if value.strip().startswith("+"):
        return f"+{digits}"
    if digits.startswith("996") and len(digits) == 12:
        return f"+{digits}"
    if len(digits) == 10 and digits.startswith("0"):
        return f"+996{digits[1:]}"
    if len(digits) == 9:
        return f"+996{digits}"
    return digits


def parse_smart_order_text(text: str) -> dict:
    """Extract a route, optional departure date, and optional seats from one message."""
    content = " ".join((text or "").strip().split())
    if not content:
        return {"route": None, "date_text": None, "seats": None, "whole_car": False}

    date_match = SMART_DATE_PATTERN.search(content)
    date_text = date_match.group(0) if date_match else None
    remainder = content[:date_match.start()] + " " + content[date_match.end():] if date_match else content

    whole_car = bool(re.search(r"(?i)\b(?:машина\s+целиком|толугу\s+менен|бүтүн\s+(?:унаа|машина))\b", remainder))
    seats = None
    seat_match = re.search(r"(?i)(?<!\d)([1-6])\s*(?:мест(?:о|а)?|орун)(?!\w)", remainder)
    if seat_match:
        seats = int(seat_match.group(1))
        remainder = remainder[:seat_match.start()] + " " + remainder[seat_match.end():]
    elif whole_car:
        seats = 4
    else:
        # Also accept a bare count at the end, e.g. "Бишкек — Ош завтра, 2".
        bare_count = re.search(r"(?:^|[,;\s])([1-6])\s*$", remainder)
        if bare_count:
            seats = int(bare_count.group(1))
            remainder = remainder[:bare_count.start()] + " "

    if whole_car:
        remainder = re.sub(
            r"(?i)\b(?:машина\s+целиком|толугу\s+менен|бүтүн\s+(?:унаа|машина))\b",
            " ", remainder,
        )
    remainder = re.sub(r"(?i)\b(?:вечером|кечинде)\b", " ", remainder)
    remainder = re.sub(r"[,;]+", " ", remainder).strip(" \t,;.")
    route_parts = re.split(r"\s*(?:->|→|➔|—|–)\s*|\s+-\s+", remainder, maxsplit=1)
    if len(route_parts) != 2:
        # Prefer whitespace between cities before treating a hyphen as a route
        # separator, so "Бишкек Чолпон-Ата" keeps the destination intact.
        words = remainder.split()
        if len(words) >= 2:
            route_parts = [words[0], " ".join(words[1:])]
        else:
            # Legacy compact route input such as "Бишкек-Ош".
            route_parts = re.split(r"\s*-\s*", remainder, maxsplit=1)
    if len(route_parts) != 2:
        return {"route": None, "date_text": date_text, "seats": seats, "whole_car": whole_car}
    origin, destination = (part.strip(" \t,;.") for part in route_parts)
    if not origin or not destination or max(len(origin), len(destination)) > 100:
        return {"route": None, "date_text": date_text, "seats": seats, "whole_car": whole_car}
    return {
        "route": (origin, destination),
        "date_text": date_text,
        "seats": seats,
        "whole_car": whole_car,
    }

async def send_inline_prompt(message: Message, state: FSMContext, text: str, markup, parse_mode=None):
    """Edit the active order prompt in place, sending a new one only as a fallback."""
    data = await state.get_data()
    active_message_id = data.get("active_inline_message_id")
    formatting = {"parse_mode": parse_mode} if parse_mode is not None else {}
    if active_message_id:
        try:
            edited = await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=active_message_id,
                text=text,
                reply_markup=markup,
                **formatting,
            )
            return edited
        except TelegramAPIError:
            # A message can be unavailable for editing after a long pause or
            # when the stored FSM data belongs to another chat message.
            pass
    sent = await message.answer(text, reply_markup=markup, **formatting)
    await state.update_data(active_inline_message_id=sent.message_id)
    return sent

async def dismiss_inline(callback: CallbackQuery) -> None:
    """Remove inline controls after a callback so the same choice cannot be reused."""
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass

async def reject_stale_order_callback(callback: CallbackQuery, state: FSMContext) -> bool:
    """Reject callbacks from older order messages and explain that the button expired."""
    data = await state.get_data()
    start_message_id = data.get("flow_start_message_id")
    if callback.message and start_message_id and callback.message.message_id >= start_message_id:
        return False
    await dismiss_inline(callback)
    await callback.answer(tr(user_language(data), "stale_keyboard"), show_alert=True)
    return True

def parse_departure_date(value: str) -> tuple[date, int | None]:
    """Parse a date and optional hour; minute precision is intentionally unsupported."""
    timezone = ZoneInfo(settings.timezone)
    today = datetime.now(timezone).date()
    text = value.strip().lower().replace("ё", "е")
    relative_match = re.fullmatch(r"(сегодня|завтра|послезавтра|вечером|бугун|бүгүн|кечинде|кечке маал|эртен|эртең|бүрсүгүн|бүрсүгүнү)(?:\s+(?:(?:в|саат)\s*)?(\d{1,2}))?", text)
    named_match = re.fullmatch(r"(\d{1,2})\s+([а-яёөүң]+)(?:\s+(\d{4}))?(?:\s+(?:(?:в|саат)\s*)?(\d{1,2}))?", text)
    numeric_match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}))?(?:\s+(?:(?:в|саат)\s*)?(\d{1,2}))?", text)
    try:
        if relative_match:
            day_word = relative_match.group(1)
            offset_days = 2 if day_word in {"послезавтра", "бүрсүгүн", "бүрсүгүнү"} else 1 if day_word in {"завтра", "эртең", "эртен"} else 0
            departure_date = today + timedelta(days=offset_days)
            hour_value = relative_match.group(2)
        elif named_match or numeric_match:
            match = named_match or numeric_match
            day = int(match.group(1))
            month = MONTH_NAMES.get(match.group(2)) if named_match else int(match.group(2))
            if month is None:
                raise ValueError("unknown month")
            explicit_year = match.group(3)
            hour_value = match.group(4) if named_match else match.group(4)
            year = int(explicit_year) if explicit_year else today.year
            departure_date = date(year, month, day)
            if not explicit_year and departure_date < today:
                while True:
                    year += 1
                    try:
                        departure_date = date(year, month, day)
                        break
                    except ValueError:
                        continue
        else:
            raise ValueError("unsupported date format")
        hour = int(hour_value) if hour_value is not None else None
        if hour is not None and not 0 <= hour <= 23:
            raise ValueError("hour out of range")
        if departure_date < today:
            raise ValueError("departure date is in the past")
        if hour is not None:
            departure = datetime.combine(departure_date, time(hour=hour), tzinfo=timezone)
            if departure <= datetime.now(timezone):
                raise ValueError("departure hour has passed")
        return departure_date, hour
    except (OverflowError, ValueError, TypeError) as exc:
        raise ValueError("invalid date") from exc

def interval_departure(departure_date: date, period: str) -> datetime:
    """Choose a valid departure hour inside the selected time interval."""
    timezone = ZoneInfo(settings.timezone)
    start_hour, end_hour = PERIODS[period]
    start = datetime.combine(departure_date, time(start_hour), tzinfo=timezone)
    end_date = departure_date + timedelta(days=1) if period == "night" else departure_date
    end = datetime.combine(end_date, time(end_hour), tzinfo=timezone)
    now = datetime.now(timezone)
    if departure_date == now.date() and start <= now:
        # For a same-day window, choose the next whole hour still inside it.
        candidate = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        candidate = start
    if candidate >= end or candidate <= now:
        raise ValueError("time window already passed")
    return candidate

def format_departure(value: datetime, period: str, language: str) -> str:
    """Format a stored departure time for the selected language and interval."""
    local = value.astimezone(ZoneInfo(settings.timezone))
    shown_date = local.date()
    if period == "night" and local.hour < 6:
        shown_date -= timedelta(days=1)
    date_text = shown_date.strftime("%d.%m.%Y")
    if period == "exact":
        return f"{date_text} {tr(language, 'hour_format', hour=local.hour)}"
    return tr(language, "period_format", date=date_text, period=tr(language, f"period_{period}"))

async def ask_seats(message: Message, state: FSMContext, language: str) -> None:
    """Move the FSM to seat selection and render the choices in the active prompt."""
    await state.set_state(OrderFlow.seats)
    await send_inline_prompt(message, state, tr(language, "seats_prompt"), seats_keyboard(language))


async def ask_phone(message: Message, state: FSMContext, language: str) -> None:
    """Advance directly to phone collection after date and seats are known."""
    await state.update_data(trip_type="passenger", departure_period="exact")
    await state.set_state(OrderFlow.phone)
    sent = await message.answer(
        tr(language, "phone_prompt"),
        reply_markup=phone_entry_keyboard(language),
        parse_mode=ParseMode.HTML,
    )
    await state.update_data(active_inline_message_id=sent.message_id)


async def advance_after_date(
    message: Message,
    state: FSMContext,
    departure_date: date,
    language: str,
    departure_hour: int | None = None,
) -> None:
    """Store a default 09:00 departure and ask only for values still missing."""
    timezone = ZoneInfo(settings.timezone)
    hour = departure_hour if departure_hour is not None else 9
    departure_at = datetime.combine(departure_date, time(hour=hour), tzinfo=timezone)
    if departure_hour is None and departure_at <= datetime.now(timezone):
        departure_at = datetime.now(timezone).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    await state.update_data(
        departure_date=departure_date.isoformat(),
        departure_at=departure_at.isoformat(),
        departure_period="exact",
    )
    data = await state.get_data()
    if data.get("seats") is None:
        await ask_seats(message, state, language)
    else:
        await ask_phone(message, state, language)


async def prompt_inline_and_remove_reply_keyboard(message: Message, state: FSMContext, text: str, markup, parse_mode=None):
    """Send an inline step while removing the previous reply keyboard in the same chat update."""
    data = await state.get_data()
    previous_id = data.get("active_inline_message_id")
    formatting = {"parse_mode": parse_mode} if parse_mode is not None else {}
    sent = await message.answer(text, reply_markup=ReplyKeyboardRemove())
    try:
        if markup is not None:
            await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            reply_markup=markup,
            )
    except TelegramAPIError:
        # Avoid leaving both copies of the prompt in the chat if attaching
        # inline controls to the keyboard-removal message fails.
        try:
            await message.bot.delete_message(message.chat.id, sent.message_id)
            sent = await message.answer(text, reply_markup=markup, **formatting)
        except TelegramAPIError:
            logger.exception("Could not attach inline keyboard to order prompt")
    if previous_id and previous_id != sent.message_id:
        try:
            await message.bot.delete_message(message.chat.id, previous_id)
        except TelegramAPIError:
            pass
    await state.update_data(active_inline_message_id=sent.message_id)
    return sent


async def ask_trip_type(message: Message, state: FSMContext, language: str) -> None:
    """Advance to passenger-versus-parcel selection after route, date, time, and seats."""
    await state.set_state(OrderFlow.trip_type)
    await send_inline_prompt(
        message,
        state,
        tr(language, "trip_prompt"),
        choice_keyboard("type", [
            (tr(language, "trip_passenger"), "passenger"),
            (tr(language, "trip_parcel"), "parcel"),
        ]),
    )


async def after_time_step(message: Message, state: FSMContext, language: str) -> None:
    """Ask for seats only when the smart order parser did not already find a count."""
    data = await state.get_data()
    if data.get("seats") is not None:
        await ask_trip_type(message, state, language)
    else:
        await ask_seats(message, state, language)


async def ask_time(message: Message, state: FSMContext, departure_date: date, language: str) -> None:
    """Store the chosen date and render the departure-time interval buttons."""
    await state.update_data(departure_date=departure_date.isoformat())
    await state.set_state(OrderFlow.departure_time)
    await prompt_inline_and_remove_reply_keyboard(
        message,
        state,
        tr(language, "time_prompt"),
        departure_period_keyboard(language),
    )

async def save_time_choice(state: FSMContext, departure_at: datetime, period: str, message: Message, language: str) -> None:
    """Store a validated time selection and advance to seat selection."""
    await state.update_data(departure_at=departure_at.isoformat(), departure_period=period)
    await after_time_step(message, state, language)

async def send_confirmation(message: Message, state: FSMContext, data: dict, language: str) -> None:
    """Render the complete order summary and phone warning for passenger confirmation."""
    departure_at = datetime.fromisoformat(data["departure_at"])
    departure = format_departure(departure_at, data["departure_period"], language)
    seats = tr(language, "whole_car_value") if data["whole_car"] else str(data["seats"])
    trip = tr(language, "trip_parcel_value" if data["trip_type"] == "parcel" else "trip_passenger_value")
    phone = data.get("phone") or ""
    phone_text = phone if phone else tr(language, "phone_missing")
    warning_text = f"⚠️ {tr(language, 'warning')}\n\n" if phone else ""
    text = (
        f"{tr(language, 'review')}\n"
        f"{tr(language, 'route_label')}: {data['route']}\n"
        f"{tr(language, 'departure_label')}: {departure}\n"
        f"{tr(language, 'seats_label')}: {seats}\n"
        f"{tr(language, 'trip_label')}: {trip}\n"
        f"{tr(language, 'phone_label')}: {phone_text}\n\n"
        f"{warning_text}"
        f"{tr(language, 'confirm_button')}?"
    )
    # Leave the phone state before sending anything so duplicate phone updates
    # cannot re-enter the handler while the confirmation is being rendered.
    await state.set_state(OrderFlow.confirm)
    await prompt_inline_and_remove_reply_keyboard(
        message,
        state,
        text,
        confirm_keyboard(language),
    )


async def update_driver_order_cancelled(bot, order_id: int, chat_id: int, message_id: int | None) -> None:
    """Mark the stored driver card cancelled, or post a fallback notice if editing fails."""
    cancelled_text = tr("ru", "driver_order_cancelled", order_id=order_id)
    if message_id is not None:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=cancelled_text,
                reply_markup=None,
            )
            return
        except TelegramAPIError:
            logger.exception("Could not edit drivers-chat card for cancelled order %s", order_id)
    try:
        await bot.send_message(chat_id, cancelled_text)
    except TelegramAPIError:
        logger.exception("Could not notify drivers about cancelled order %s", order_id)


@router.message(F.text.in_({tr("ru", "cancel_order"), tr("ky", "cancel_order")}))
async def cancel_order(message: Message, state: FSMContext, session):
    """Clear an in-progress order and restore the passenger's main menu."""
    data = await state.get_data()
    user = await get_user(session, message.from_user.id)
    language = (user.language_code if user else None) or user_language(data)
    await state.clear()
    old_keyboard_id = data.get("active_inline_message_id")
    if old_keyboard_id:
        try:
            await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=old_keyboard_id, reply_markup=None)
        except TelegramAPIError:
            pass
    await message.answer(tr(language, "cancelled_flow"), reply_markup=main_menu(language))

@router.message(F.text.in_({tr("ru", "menu_order"), tr("ky", "menu_order")}))
async def begin_order(message: Message, state: FSMContext, session):
    """Start the compact free-text order flow in the client's saved language."""
    user = await get_user(session, message.from_user.id)
    if user is None or user.role != "client":
        language = user.language_code if user else "ru"
        await message.answer(tr(language, "client_only"))
        return
    language = user.language_code or "ru"
    await start_smart_order(message, state, language)


async def start_smart_order(message: Message, state: FSMContext, language: str) -> None:
    """Reset the previous FSM flow and ask for route, departure date, and seats together."""
    await state.clear()
    await state.update_data(language=language)
    await state.set_state(OrderFlow.smart_order)
    prompt = await message.answer(
        tr(language, "smart_order_prompt"),
        reply_markup=cancel_order_keyboard(language),
        parse_mode=ParseMode.HTML,
    )
    await state.update_data(
        active_inline_message_id=prompt.message_id,
        flow_start_message_id=prompt.message_id,
    )


async def ask_smart_order_text(message: Message, state: FSMContext, language: str) -> None:
    """Switch an existing route picker to the free-text route/date/seat prompt."""
    await state.set_state(OrderFlow.smart_order)
    prompt = await send_inline_prompt(
        message,
        state,
        tr(language, "smart_order_prompt"),
        None,
        parse_mode=ParseMode.HTML,
    )
    await state.update_data(flow_start_message_id=prompt.message_id)

@router.callback_query(OrderFlow.route, F.data == "route:other")
async def show_other_routes(callback: CallbackQuery, state: FSMContext, session):
    """Let the client type any route, departure date, and seat count."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    await ask_smart_order_text(callback.message, state, language)
    await callback.answer()

@router.callback_query(OrderFlow.more_routes, F.data == "route:custom")
async def begin_custom_route(callback: CallbackQuery, state: FSMContext):
    """Switch to free-text route/date/seat entry and update the active prompt."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    await ask_smart_order_text(callback.message, state, language)
    await callback.answer()

async def select_route(callback: CallbackQuery, state: FSMContext, session):
    """Validate the selected route, store it, and prompt for a departure date."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    data = await state.get_data()
    language = user_language(data)
    try:
        route_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer(tr(language, "route_unavailable"), show_alert=True)
        return
    routes = await active_routes(session)
    route = next((item for item in routes if item.id == route_id), None)
    if not route:
        await callback.answer(tr(language, "route_unavailable"), show_alert=True)
        return
    await state.update_data(route_id=route.id, route=f"{route.origin} — {route.destination}")
    await state.set_state(OrderFlow.departure)
    await send_inline_prompt(callback.message, state, f"{route.origin} — {route.destination}\n{tr(language, 'departure_prompt')}", None)
    await callback.answer()

@router.callback_query(OrderFlow.route, F.data.startswith("route:"))
async def choose_popular_route(callback: CallbackQuery, state: FSMContext, session):
    """Handle a route selected from the compact popular-route menu."""
    await select_route(callback, state, session)

@router.callback_query(OrderFlow.more_routes, F.data.startswith("route:"))
async def choose_other_route(callback: CallbackQuery, state: FSMContext, session):
    """Handle a route selected from the expanded route menu."""
    await select_route(callback, state, session)

@router.message(OrderFlow.custom_route)
async def set_custom_route(message: Message, state: FSMContext, session):
    """Validate and save a manually entered route before requesting a date."""
    language = user_language(await state.get_data())
    parts = re.split(r"\s*[-–—]\s*", (message.text or "").strip(), maxsplit=1)
    if len(parts) != 2 or not all(parts) or max(map(len, parts)) > 100:
        await send_inline_prompt(message, state, tr(language, "custom_route_invalid"), None)
        return
    origin, destination = (part.strip() for part in parts)
    route = await get_or_create_custom_route(session, origin, destination)
    route_name = f"{route.origin} — {route.destination}"
    await state.update_data(route_id=route.id, route=route_name)
    await state.set_state(OrderFlow.departure)
    await send_inline_prompt(message, state, f"{route_name}\n{tr(language, 'departure_prompt')}", None)


async def prompt_for_departure_date(message: Message, state: FSMContext, route_name: str, language: str) -> None:
    """Ask for a date with a single row of localized inline quick-choice buttons."""
    await state.set_state(OrderFlow.departure)
    await prompt_inline_and_remove_reply_keyboard(
        message,
        state,
        tr(language, "date_inline_prompt"),
        departure_date_inline_keyboard(language),
        parse_mode=ParseMode.HTML,
    )


@router.message(OrderFlow.smart_order, F.text)
async def parse_smart_order(message: Message, state: FSMContext, session) -> None:
    """Parse a free-text route, date, and seat count, then ask only for missing values."""
    data = await state.get_data()
    language = user_language(data)
    parsed = parse_smart_order_text(message.text or "")
    if parsed["route"] is None:
        await send_inline_prompt(message, state, tr(language, "smart_order_invalid"), None)
        return

    origin, destination = parsed["route"]
    route = await get_or_create_custom_route(session, origin, destination)
    route_name = f"{route.origin} — {route.destination}"
    await state.update_data(
        route_id=route.id,
        route=route_name,
        seats=parsed["seats"],
        whole_car=parsed["whole_car"],
    )

    date_text = parsed["date_text"]
    if date_text is None:
        await state.set_state(OrderFlow.departure)
        await prompt_for_departure_date(message, state, route_name, language)
        return

    try:
        departure_date, hour = parse_departure_date(date_text)
        if hour is None and date_text.strip().lower() in {"кечинде", "вечером"}:
            hour = 19
    except ValueError:
        await message.answer(tr(language, "departure_invalid"))
        await state.set_state(OrderFlow.departure)
        await prompt_for_departure_date(message, state, route_name, language)
        return

    await advance_after_date(message, state, departure_date, language, hour)


@router.callback_query(OrderFlow.departure, F.data.startswith("date:"))
async def choose_departure_date_inline(callback: CallbackQuery, state: FSMContext):
    """Resolve one of the inline relative-date choices and continue the order."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    choice = callback.data.split(":", 1)[1]
    offsets = {"today": 0, "tomorrow": 1, "day_after_tomorrow": 2}
    if choice not in offsets:
        await callback.answer(tr(language, "stale_keyboard"), show_alert=True)
        return
    departure_date = datetime.now(ZoneInfo(settings.timezone)).date() + timedelta(days=offsets[choice])
    await advance_after_date(callback.message, state, departure_date, language)
    await callback.answer()

@router.message(OrderFlow.departure)
async def choose_departure_date(message: Message, state: FSMContext):
    """Parse passenger date input and either store its hour or offer time intervals."""
    language = user_language(await state.get_data())
    try:
        departure_date, hour = parse_departure_date(message.text or "")
    except (ValueError, AttributeError, TypeError):
        await message.answer(
            tr(language, "departure_invalid"),
        )
        data = await state.get_data()
        await prompt_for_departure_date(
            message,
            state,
            data.get("route", ""),
            language,
        )
        return
    await advance_after_date(message, state, departure_date, language, hour)

@router.callback_query(OrderFlow.departure_time, F.data.startswith("period:"))
async def choose_time_period(callback: CallbackQuery, state: FSMContext):
    """Store a selected interval or replace it with a grid of exact hours."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    choice = callback.data.split(":", 1)[1]
    if choice == "exact":
        data = await state.get_data()
        departure_date = date.fromisoformat(data["departure_date"])
        hours = list(range(24))
        timezone = ZoneInfo(settings.timezone)
        now = datetime.now(timezone)
        if departure_date == now.date():
            first_future_hour = now.hour + 1
            hours = [hour for hour in hours if hour >= first_future_hour]
        if not hours:
            await callback.answer(tr(language, "time_passed"), show_alert=True)
            return
        await state.set_state(OrderFlow.exact_hour)
        await send_inline_prompt(callback.message, state, tr(language, "hour_prompt"), exact_hours_keyboard(hours, language))
        await callback.answer()
        return
    if choice not in PERIODS:
        await callback.answer()
        return
    data = await state.get_data()
    departure_date = date.fromisoformat(data["departure_date"])
    try:
        departure_at = interval_departure(departure_date, choice)
    except ValueError:
        await callback.answer(tr(language, "time_passed"), show_alert=True)
        return
    await state.update_data(
        departure_date=departure_date.isoformat(),
        departure_at=departure_at.isoformat(),
        departure_period=choice,
    )
    await after_time_step(callback.message, state, language)
    await callback.answer()

@router.callback_query(OrderFlow.exact_hour, F.data.startswith("hour:"))
async def choose_exact_hour(callback: CallbackQuery, state: FSMContext):
    """Validate the selected hour and advance the order to seat selection."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    data = await state.get_data()
    departure_date = date.fromisoformat(data["departure_date"])
    try:
        hour = int(callback.data.split(":", 1)[1])
        departure_at = datetime.combine(departure_date, time(hour), tzinfo=ZoneInfo(settings.timezone))
        if departure_at <= datetime.now(ZoneInfo(settings.timezone)):
            raise ValueError
    except ValueError:
        await callback.answer(tr(language, "time_passed"), show_alert=True)
        return
    await state.update_data(departure_at=departure_at.isoformat(), departure_period="exact")
    await after_time_step(callback.message, state, language)
    await callback.answer()

@router.callback_query(OrderFlow.seats, F.data.startswith("seats:"))
async def choose_seats(callback: CallbackQuery, state: FSMContext):
    """Store the requested seat count and show passenger or parcel trip types."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    value = callback.data.split(":", 1)[1]
    try:
        seats = 4 if value == "car" else int(value)
        if not 1 <= seats <= 6:
            raise ValueError
    except ValueError:
        await callback.answer(tr(language, "stale_keyboard"), show_alert=True)
        return
    await state.update_data(seats=seats, whole_car=value == "car")
    await ask_phone(callback.message, state, language)
    await callback.answer()

@router.callback_query(OrderFlow.trip_type, F.data.startswith("type:"))
async def choose_type(callback: CallbackQuery, state: FSMContext):
    """Store the trip type and request the passenger's contact phone number."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    await state.update_data(trip_type=callback.data.split(":", 1)[1])
    await state.set_state(OrderFlow.phone)
    sent = await callback.message.answer(
        tr(language, "phone_prompt"),
        reply_markup=phone_entry_keyboard(language),
        parse_mode=ParseMode.HTML,
    )
    await state.update_data(active_inline_message_id=sent.message_id)
    try:
        await callback.message.delete()
    except TelegramAPIError:
        pass
    await callback.answer()

@router.callback_query(OrderFlow.confirm, F.data == "confirm:phone")
async def change_phone(callback: CallbackQuery, state: FSMContext):
    """Return from confirmation to the phone-entry step."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    language = user_language(await state.get_data())
    await state.set_state(OrderFlow.phone)
    sent = await callback.message.answer(
        tr(language, "phone_prompt"),
        reply_markup=phone_entry_keyboard(language),
        parse_mode=ParseMode.HTML,
    )
    await state.update_data(active_inline_message_id=sent.message_id)
    try:
        await callback.message.delete()
    except TelegramAPIError:
        pass
    await callback.answer()

@router.message(OrderFlow.phone)
async def receive_phone(message: Message, state: FSMContext):
    """Accept a shared or typed phone, or explicitly continue without a number."""
    language = user_language(await state.get_data())
    without_phone_labels = {
        tr("ru", "continue_without_phone"),
        tr("ky", "continue_without_phone"),
    }
    if message.text in without_phone_labels:
        await state.update_data(phone="")
        await send_confirmation(message, state, await state.get_data(), language)
        return

    if message.contact:
        if message.contact.user_id != message.from_user.id:
            await message.answer(tr(language, "phone_invalid"))
            return
        raw_numbers = [message.contact.phone_number]
    else:
        raw_numbers = [part.strip() for part in (message.text or "").split(",")]

    raw_numbers = [part.strip() for part in raw_numbers if part.strip()]
    valid_numbers = (
        1 <= len(raw_numbers) <= 2
        and all(
            re.fullmatch(r"\+?[()\d .-]+", number)
            and 9 <= len(re.sub(r"\D", "", number)) <= 12
            for number in raw_numbers
        )
    )
    normalized_numbers = [normalize_phone_number(number) for number in raw_numbers]
    normalized = ", ".join(normalized_numbers)
    if not valid_numbers or len(normalized) > 32:
        await send_inline_prompt(message, state, tr(language, "phone_invalid"), None)
        return
    await state.update_data(phone=normalized)
    await send_confirmation(message, state, await state.get_data(), language)

@router.callback_query(OrderFlow.confirm, F.data.startswith("confirm:"))
async def confirm_order(callback: CallbackQuery, state: FSMContext, session):
    """Persist the confirmed order, notify drivers, and report delivery failures safely."""
    if await reject_stale_order_callback(callback, state):
        return
    await dismiss_inline(callback)
    data = await state.get_data()
    language = user_language(data)
    action = callback.data.split(":", 1)[1]
    if action == "no":
        await state.clear()
        await callback.message.answer(tr(language, "cancelled"), reply_markup=main_menu(language))
        await callback.answer()
        return
    if action != "yes":
        await callback.answer(tr(language, "stale_keyboard"), show_alert=True)
        return
    await callback.answer(tr(language, "saving"))
    passenger = await upsert_user(
        session,
        callback.from_user,
        phone=data["phone"] or None,
        language=language,
    )
    order = await make_order(session, passenger, data["route_id"], datetime.fromisoformat(data["departure_at"]), data["seats"], data["whole_car"], data["trip_type"], data["phone"], data["departure_period"])
    await session.flush()
    full = await order_with_details(session, order.id)
    await session.commit()
    drivers_count = await get_active_drivers_count(
        session,
        full.route.origin,
        full.route.destination,
        vip_only=bool(full.whole_car or full.seats >= 4),
    )
    await state.clear()
    await send_order_to_active_drivers(callback.bot, full.id)
    local_departure = full.departure_at.astimezone(ZoneInfo(settings.timezone))
    departure_text = local_departure.strftime("%d.%m.%Y")
    if full.whole_car:
        seats_text = tr(language, "whole_car_value")
    else:
        count = full.seats
        if language == "ky":
            seat_unit = tr(language, "searching_seat_many")
        elif count % 10 == 1 and count % 100 != 11:
            seat_unit = tr(language, "searching_seat_one")
        elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
            seat_unit = tr(language, "searching_seat_few")
        else:
            seat_unit = tr(language, "searching_seat_many")
        seats_text = f"{count} {seat_unit}"
    status_text = tr(
        language,
        "searching_with_drivers" if drivers_count > 0 else "searching_no_drivers",
        drivers_count=drivers_count,
    )
    search_card = (
        f"{tr(language, 'searching_header')}\n\n"
        f"🚘 <b>{full.route.origin} ➔ {full.route.destination}</b>\n"
        f"📅 <b>{departure_text}</b> · 👥 <b>{seats_text}</b>\n\n"
        f"{status_text}\n\n"
        f"{tr(language, 'searching_footer')}"
    )
    await callback.message.answer(
        search_card,
        reply_markup=found_car_keyboard(full.id, language),
        parse_mode=ParseMode.HTML,
    )
    await callback.message.answer(tr(language, "client_menu_prompt"), reply_markup=main_menu(language))

@router.message(F.text.in_({tr("ru", "menu_orders"), tr("ky", "menu_orders")}))
async def my_orders(message: Message, session):
    """Show recent order cards with cancellation controls only for active orders."""
    user = await get_user(session, message.from_user.id)
    language = user.language_code if user else "ru"
    if user is None or user.role != "client":
        await message.answer(tr(language, "client_only"))
        return
    orders = [
        order for order in await passenger_orders(session, user.id)
        if order.status == "pending"
    ]
    if not orders:
        await message.answer(tr(language, "orders_empty"), reply_markup=main_menu(language))
        return
    status_key = {
        "pending": "status_pending",
    }
    await message.answer(tr(language, "orders_title"), reply_markup=main_menu(language))
    for order in orders:
        route = f"{order.route.origin} — {order.route.destination}"
        seats = tr(language, "whole_car_value") if order.whole_car else str(order.seats)
        status = tr(language, status_key.get(order.status, "status_pending"))
        text = tr(
            language,
            "order_item",
            route=route,
            seats=seats,
            phone=order.phone or tr(language, "phone_missing"),
            status=status,
        )
        keyboard = (
            found_car_keyboard(order.id, language)
            if order.status in {"pending", "active"}
            else None
        )
        await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("canceltrip:"))
async def cancel_trip(callback: CallbackQuery, session):
    """Cancel only the caller's pending order and synchronize both Telegram cards."""
    language = "ru"
    user = await get_user(session, callback.from_user.id)
    language = (user.language_code if user else None) or language
    if user is None:
        await callback.answer(tr(language, "cancel_not_found"), show_alert=True)
        return
    try:
        order_id = int(callback.data.rsplit(":", 1)[1])
        if order_id < 1:
            raise ValueError
    except (ValueError, IndexError):
        await callback.answer(tr(language, "cancel_not_found"), show_alert=True)
        return

    status, driver_chat_id, driver_message_id, changed = await cancel_passenger_order(
        session,
        order_id,
        callback.from_user.id,
    )
    if status is None:
        await callback.answer(tr(language, "cancel_not_found"), show_alert=True)
        return
    if not changed:
        if status in {"accepted", "in_progress"}:
            contact = (
                f"@{settings.support_username.lstrip('@')}"
                if settings.support_username
                else tr(language, "support_missing")
            )
            await callback.answer(
                tr(language, "cancel_accepted", contact=contact),
                show_alert=True,
            )
        elif status == "cancelled":
            await callback.answer(tr(language, "cancel_already"), show_alert=True)
        else:
            await callback.answer(tr(language, "cancel_not_allowed"), show_alert=True)
        return

    # Commit the status before Telegram API calls: external edits cannot share
    # the PostgreSQL transaction, so a Telegram outage must not undo cancellation.
    await session.commit()
    await synchronize_order_cancellation(callback.bot, session, order_id)
    if driver_chat_id is not None and driver_message_id is not None:
        await update_driver_order_cancelled(
            callback.bot,
            order_id,
            driver_chat_id,
            driver_message_id,
        )

    try:
        await callback.message.edit_text(
            tr(language, "cancelled_order_card", order_id=order_id),
            reply_markup=None,
        )
    except TelegramAPIError:
        logger.exception("Could not update passenger order card for cancelled order %s", order_id)
        await callback.message.answer(tr(language, "cancelled_order_card", order_id=order_id))
    await callback.answer(tr(language, "cancel_trip_alert"), show_alert=True)
