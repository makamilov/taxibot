from urllib.parse import urlencode

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.i18n import tr


class FoundCarCallback(CallbackData, prefix="cancel", sep="_"):
    """Identify the passenger's order they have already matched with a car."""
    action: str
    order_id: int


class ReturnOrderCallback(CallbackData, prefix="return", sep="_"):
    """Identify a return-trip request and the relative departure-date choice."""
    action: str
    order_id: int
    when: str


class DriverRouteCallback(CallbackData, prefix="driverroute"):
    """Identify one route checkbox selected by a driver."""
    route_id: int


def share_friend_url(bot_username: str, user_id: int, language: str = "ru") -> str:
    """Build Telegram's share URL carrying the bot referral deep link."""
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    query = urlencode({"url": referral_link, "text": tr(language, "share_text")})
    return f"https://t.me/share/url?{query}"


def share_friend_button(bot_username: str, user_id: int, language: str = "ru"):
    """Build the inline URL button that opens Telegram's chat-sharing chooser."""
    return InlineKeyboardButton(
        text=tr(language, "invite_button"),
        url=share_friend_url(bot_username, user_id, language),
    )


def invite_friend_keyboard(bot_username: str, user_id: int, language: str = "ru"):
    """Build a one-button invite keyboard for the current user."""
    return InlineKeyboardMarkup(inline_keyboard=[[share_friend_button(bot_username, user_id, language)]])


def found_car_keyboard(order_id: int, language: str = "ru"):
    """Build the completion action attached to a passenger order card."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=tr(language, "found_car"),
            callback_data=FoundCarCallback(action="search", order_id=order_id).pack(),
        )
    ]])


def return_trip_keyboard(order_id: int, bot_username: str, user_id: int, language: str = "ru"):
    """Build quick date choices plus the referral action for a return trip."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "return_tomorrow"), callback_data=ReturnOrderCallback(action="order", order_id=order_id, when="tomorrow").pack())],
        [InlineKeyboardButton(text=tr(language, "return_in_2_days"), callback_data=ReturnOrderCallback(action="order", order_id=order_id, when="in-2-days").pack())],
        [InlineKeyboardButton(text=tr(language, "return_later"), callback_data="return_order_cancel")],
        [share_friend_button(bot_username, user_id, language)],
    ])

def language_keyboard():
    """Build the inline language selector shown to first-time users."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Кыргызча 🇰🇬", callback_data="language:ky"), InlineKeyboardButton(text="Русский 🇷🇺", callback_data="language:ru")]])


def language_choice_keyboard():
    """Build the shared language selector for client and driver settings."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇰🇬 Кыргызча", callback_data="set_lang_ky")],
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang_ru")],
    ])


def role_keyboard(language: str = "ru"):
    """Build the inline selector for the client's or driver's bot role."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(language, "role_client"), callback_data="role:client"),
        InlineKeyboardButton(text=tr(language, "role_driver"), callback_data="role:driver"),
    ]])


def subscription_keyboard(language: str, price_7: int, price_30: int, currency: str):
    """Build the available subscription-plan buttons with configured prices."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "subscription_plan_7", amount=price_7, currency=currency), callback_data="subpay:7")],
        [InlineKeyboardButton(text=tr(language, "subscription_plan_30", amount=price_30, currency=currency), callback_data="subpay:30")],
    ])


def vip_subscription_keyboard(language: str, price_7: int, price_30: int, currency: str):
    """Build separate checkout buttons for one-week and one-month VIP access."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "vip_plan_7", amount=price_7, currency=currency), callback_data="vippay:7")],
        [InlineKeyboardButton(text=tr(language, "vip_plan_30", amount=price_30, currency=currency), callback_data="vippay:30")],
    ])

def route_keyboard(routes, language: str = "ru"):
    """Build a compact list of popular destinations and the other-routes action."""
    popular = {("бишкек", "ош"), ("бишкек", "каракол"), ("бишкек", "балыкчы"), ("бишкек", "чолпон-ата")}
    rows = [[InlineKeyboardButton(text=f"{r.origin} — {r.destination}", callback_data=f"route:{r.id}")] for r in routes if (r.origin.casefold(), r.destination.casefold()) in popular]
    rows.append([InlineKeyboardButton(text=tr(language, "other_route"), callback_data="route:other")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def more_routes_keyboard(routes, language: str = "ru"):
    """Build the expanded destination list with an option for a custom route."""
    rows = [[InlineKeyboardButton(text=f"{r.origin} — {r.destination}", callback_data=f"route:{r.id}")] for r in routes]
    rows.append([InlineKeyboardButton(text=tr(language, "manual_route"), callback_data="route:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def departure_period_keyboard(language: str = "ru"):
    """Build the four departure-interval choices and exact-hour action."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=tr(language, "period_morning"), callback_data="period:morning"), InlineKeyboardButton(text=tr(language, "period_lunch"), callback_data="period:lunch")],
        [InlineKeyboardButton(text=tr(language, "period_evening"), callback_data="period:evening"), InlineKeyboardButton(text=tr(language, "period_night"), callback_data="period:night")],
        [InlineKeyboardButton(text=tr(language, "exact_time"), callback_data="period:exact")],
    ])

def exact_hours_keyboard(hours, language: str = "ru"):
    """Arrange selectable departure hours in rows of four inline buttons."""
    buttons = [InlineKeyboardButton(text=tr(language, "hour_format", hour=hour), callback_data=f"hour:{hour}") for hour in hours]
    rows = [buttons[index:index + 4] for index in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def choice_keyboard(prefix, choices):
    """Build callback buttons using a fixed prefix and supplied label/value pairs."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"{prefix}:{value}")] for label, value in choices])


def departure_date_inline_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    """Build the three relative departure-date actions in a single inline row."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(language, "date_today"), callback_data="date:today"),
        InlineKeyboardButton(text=tr(language, "date_tomorrow"), callback_data="date:tomorrow"),
        InlineKeyboardButton(text=tr(language, "date_day_after_tomorrow"), callback_data="date:day_after_tomorrow"),
    ]])


def seats_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    """Build a 3-by-2 seat grid and a full-car option on its own row."""
    labels = (
        ["1 место", "2 места", "3 места", "4 места", "5 мест", "6 мест"]
        if language != "ky"
        else [f"{count} орун" for count in range(1, 7)]
    )
    buttons = [InlineKeyboardButton(text=labels[count - 1], callback_data=f"seats:{count}") for count in range(1, 7)]
    return InlineKeyboardMarkup(inline_keyboard=[
        buttons[:3],
        buttons[3:],
        [InlineKeyboardButton(text=tr(language, "whole_car"), callback_data="seats:car")],
    ])

def confirm_keyboard(language: str = "ru"):
    """Build the confirm, edit-phone, and cancel actions for an order summary."""
    return choice_keyboard("confirm", [(tr(language, "confirm_button"), "yes"), (tr(language, "edit_phone"), "phone"), (tr(language, "cancel_button"), "no")])

def cancel_trip_keyboard(order_id: int, language: str = "ru"):
    """Build the per-order cancellation button with the target order ID in callback data."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=tr(language, "cancel_trip_button"),
            callback_data=f"canceltrip:{order_id}",
        )
    ]])

def driver_order_keyboard(phone: str | None, order_id: int, language: str = "ru", passenger_id: int | None = None):
    """Build optional phone, profile, and hide actions for a driver order card."""
    contact_row = []
    if phone:
        contact_row.append(
            InlineKeyboardButton(text=tr(language, "driver_call"), callback_data=f"call_order:{order_id}")
        )
    if passenger_id:
        contact_row.append(
            InlineKeyboardButton(
                text=tr(language, "driver_profile"),
                url=f"tg://user?id={passenger_id}",
            )
        )
    rows = [contact_row] if contact_row else []
    rows.append([InlineKeyboardButton(text=tr(language, "driver_hide"), callback_data=f"hide_order:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def driver_routes_keyboard(routes, selected_ids: set[int], language: str = "ru"):
    """Build a checkbox-style inline route selector for a driver's preferences."""
    rows = []
    for route in routes:
        chosen = route.id in selected_ids
        marker = "✅" if chosen else "⬜️"
        rows.append([InlineKeyboardButton(
            text=f"{marker} {route.origin} ➔ {route.destination}",
            callback_data=DriverRouteCallback(route_id=route.id).pack(),
        )])
    rows.append([InlineKeyboardButton(
        text=tr(language, "driver_add_custom_route"),
        callback_data="add_custom_route",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)
