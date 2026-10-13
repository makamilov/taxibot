from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from bot.i18n import tr

def main_menu(language_code: str = "ru"):
    """Build the localized passenger main menu."""
    if language_code == "ky":
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text="🚗 Автоунаа табуу")],
            [KeyboardButton(text="📋 Менин сапарларым"), KeyboardButton(text="☎️ Байланышуу")],
            [KeyboardButton(text="ℹ️ Жардам"), KeyboardButton(text="📩 Досту чакыруу")],
            [KeyboardButton(text="🌐 Язык / Тил")],
            [KeyboardButton(text=tr(language_code, "menu_role"))],
        ], resize_keyboard=True)
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=tr(language_code, "menu_order"))],
        [KeyboardButton(text=tr(language_code, "menu_orders")), KeyboardButton(text=tr(language_code, "menu_support"))],
        [KeyboardButton(text=tr(language_code, "menu_invite")), KeyboardButton(text="🌐 Язык / Тил")],
        [KeyboardButton(text=tr(language_code, "menu_role"))],
    ], resize_keyboard=True)


def driver_menu(language: str = "ru", is_working: bool = True):
    """Build the four-row driver control menu with a state-dependent work toggle."""
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=tr(language, "driver_pause" if is_working else "driver_resume")), KeyboardButton(text=tr(language, "driver_extend"))],
        [KeyboardButton(text=tr(language, "driver_status")), KeyboardButton(text=tr(language, "driver_routes"))],
        [KeyboardButton(text=tr(language, "driver_parcels")), KeyboardButton(text=tr(language, "driver_contact"))],
        [KeyboardButton(text=tr(language, "driver_vip")), KeyboardButton(text=tr(language, "driver_referral"))],
        [KeyboardButton(text="🌐 Язык / Тил")],
        [KeyboardButton(text=tr(language, "driver_switch_role"))],
    ], resize_keyboard=True)

def cancel_order_keyboard(language: str = "ru"):
    """Build the persistent one-button order-cancellation keyboard."""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=tr(language, "cancel_order"))]], resize_keyboard=True, is_persistent=True)

def contact_keyboard(language: str = "ru"):
    """Build a contact-sharing button for the user's own Telegram phone number."""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=tr(language, "contact_button"), request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

def phone_entry_keyboard(language: str = "ru"):
    """Build contact-sharing and no-phone choices for the order phone step."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=tr(language, "contact_button"), request_contact=True)],
            [KeyboardButton(text=tr(language, "continue_without_phone"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def departure_date_keyboard(language: str = "ru"):
    """Build localized quick date choices while accepting a typed date as well."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=tr(language, "date_today")), KeyboardButton(text=tr(language, "date_tomorrow"))],
            [KeyboardButton(text=tr(language, "date_day_after_tomorrow"))],
            [KeyboardButton(text=tr(language, "cancel_order"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )

remove_keyboard = ReplyKeyboardRemove()
