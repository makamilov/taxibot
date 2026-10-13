from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.database.requests import get_user, upsert_user
from bot.i18n import tr
from bot.keyboards.reply import driver_menu, main_menu

router = Router()

@router.message(Command("chatid"))
async def show_chat_id(message: Message):
    """Show the current chat ID only to configured administrators."""
    if message.from_user.id not in settings.admin_ids:
        return
    await message.answer(f"ID этого чата: {message.chat.id}\nУкажите это число в .env как DRIVERS_CHAT_ID.")

@router.callback_query()
async def ignore_stale_inline_buttons(callback: CallbackQuery, session):
    """Acknowledge unmatched old inline buttons and remove their stale markup."""
    user = await get_user(session, callback.from_user.id)
    language = user.language if user and user.language else "ru"
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
    await callback.answer(tr(language, "stale_keyboard"), show_alert=True)

@router.message(F.text.in_({tr("ru", "menu_about"), tr("ky", "menu_about")}))
async def about(message: Message, session):
    """Show localized service information to a passenger."""
    user = await upsert_user(session, message.from_user)
    keyboard = driver_menu(user.language, user.is_working) if user.role == "driver" else main_menu(user.language)
    await message.answer(tr(user.language, "about"), reply_markup=keyboard)

@router.message(F.text.in_({tr("ru", "menu_support"), tr("ky", "menu_support")}))
async def support(message: Message, session):
    """Show the configured support username or localized fallback instructions."""
    user = await upsert_user(session, message.from_user)
    contact = f"@{settings.support_username.lstrip('@')}" if settings.support_username else tr(user.language, "support_missing")
    keyboard = driver_menu(user.language, user.is_working) if user.role == "driver" else main_menu(user.language)
    await message.answer(tr(user.language, "support", contact=contact), reply_markup=keyboard)
