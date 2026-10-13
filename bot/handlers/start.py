from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramAPIError

from bot.database.requests import (
    get_driver_routes,
    get_user,
    popular_driver_routes,
    set_user_role,
    upsert_user,
)
from bot.i18n import tr
from bot.keyboards.inline import (
    driver_routes_keyboard,
    language_choice_keyboard,
    language_keyboard,
    role_keyboard,
)
from bot.keyboards.reply import driver_menu, main_menu
from bot.services.subscription import reward_driver_referral, start_trial_if_needed
from bot.states.language_state import LanguageFlow

router = Router()

@router.message(CommandStart(ignore_case=True))
@router.message(Command("language"))
async def start(message: Message, state: FSMContext, session, command: CommandObject | None = None):
    """Reset any active flow and show the saved-language welcome or language chooser."""
    # Capture the old prompt before clearing FSM data, then reset all pending steps.
    previous = await state.get_data()
    await state.clear()
    if message.chat.type != "private":
        await message.answer("Пожалуйста, напишите боту в личные сообщения для оформления поездки.")
        return
    old_keyboard_id = previous.get("active_inline_message_id")
    if old_keyboard_id:
        try:
            await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=old_keyboard_id, reply_markup=None)
        except TelegramAPIError:
            pass
    user = await get_user(session, message.from_user.id)
    referrer_id = None
    if user is None and command and command.args and command.args.startswith("driverref_"):
        try:
            parsed_referrer = int(command.args.removeprefix("driverref_"))
            referrer = await get_user(session, parsed_referrer) if parsed_referrer > 0 else None
            if referrer and referrer.role == "driver" and parsed_referrer != message.from_user.id:
                referrer_id = parsed_referrer
                user = await upsert_user(
                    session, message.from_user, referrer_telegram_id=referrer_id
                )
        except ValueError:
            referrer_id = None
    force_language = message.text.startswith("/language") or message.text in {tr("ru", "menu_language"), tr("ky", "menu_language")}
    if not force_language and user and user.language in {"ru", "ky"}:
        language = user.language
        await upsert_user(session, message.from_user, language=language)
        if user.role == "driver":
            await start_trial_if_needed(session, user.telegram_id)
            await message.answer(tr(language, "driver_welcome"), reply_markup=driver_menu(language, user.is_working))
        else:
            await message.answer(tr(language, "welcome"), reply_markup=main_menu(language))
        return
    await state.set_state(LanguageFlow.selecting)
    sent = await message.answer(tr("ru", "language_prompt"), reply_markup=language_keyboard())
    await state.update_data(active_inline_message_id=sent.message_id, referrer_telegram_id=referrer_id)


@router.message(F.text == "🌐 Язык / Тил")
async def show_language_options(message: Message, session) -> None:
    """Show one language selector shared by client and driver accounts."""
    user = await upsert_user(session, message.from_user)
    language = user.language if user.language in {"ru", "ky"} else "ru"
    prompt = (
        "Интерфейстин тилин тандаңыз:"
        if language == "ky"
        else "Выберите удобный язык интерфейса:"
    )
    await message.answer(prompt, reply_markup=language_choice_keyboard())


@router.callback_query(F.data.in_({"set_lang_ky", "set_lang_ru"}))
async def save_interface_language(callback: CallbackQuery, state: FSMContext, session) -> None:
    """Persist the selected language and redraw the correct role's main menu."""
    user = await upsert_user(session, callback.from_user)
    language = "ky" if callback.data == "set_lang_ky" else "ru"
    user.language = language
    await session.commit()
    await state.update_data(language=language)

    confirmation = (
        "Тил ийгиликтүү кыргызчага өзгөртүлдү! 🇰🇬"
        if language == "ky"
        else "Язык успешно изменен на русский! 🇷🇺"
    )
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
        if user.role == "driver":
            await callback.message.answer(
                confirmation,
                reply_markup=driver_menu(language, user.is_working),
            )
        else:
            await callback.message.answer(confirmation, reply_markup=main_menu(language))
    await callback.answer()

@router.callback_query(LanguageFlow.selecting, F.data.startswith("language:"))
async def choose_language(callback: CallbackQuery, state: FSMContext, session):
    """Validate the current language button and persist the passenger's choice."""
    state_data = await state.get_data()
    if not callback.message or callback.message.message_id != state_data.get("active_inline_message_id"):
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        await callback.answer(tr("ru", "stale_keyboard"), show_alert=True)
        return
    language = callback.data.split(":", 1)[1]
    if language not in {"ru", "ky"}:
        await callback.answer()
        return
    state_data = await state.get_data()
    referrer_id = state_data.get("referrer_telegram_id")
    await state.clear()
    await state.update_data(language=language)
    await upsert_user(
        session, callback.from_user, language=language,
        referrer_telegram_id=referrer_id,
    )
    await callback.message.edit_reply_markup(reply_markup=None)
    role_prompt = await callback.message.answer(
        tr(language, "role_prompt"),
        reply_markup=role_keyboard(language),
    )
    await state.update_data(
        role_selection_message_id=role_prompt.message_id,
        referrer_telegram_id=referrer_id,
    )
    await callback.answer()


@router.message(Command("role"))
@router.message(F.text.in_({tr("ru", "menu_role"), tr("ky", "menu_role")}))
async def show_role_choices(message: Message, state: FSMContext, session):
    """Show the role selector and clear any unrelated in-progress FSM flow."""
    await state.clear()
    user = await upsert_user(session, message.from_user)
    language = user.language or "ru"
    role_prompt = await message.answer(
        tr(language, "role_prompt"),
        reply_markup=role_keyboard(language),
    )
    await state.update_data(
        language=language,
        role_selection_message_id=role_prompt.message_id,
    )


@router.message(F.text.in_({
    tr("ru", "driver_client_mode"), tr("ky", "driver_client_mode"),
    tr("ru", "driver_switch_role"), tr("ky", "driver_switch_role"),
}))
async def switch_to_client(message: Message, state: FSMContext, session):
    """Switch the user's persisted role back to client and show its menu."""
    await state.clear()
    user = await upsert_user(session, message.from_user)
    language = user.language or "ru"
    if not await set_user_role(session, user.telegram_id, "client"):
        await message.answer(tr(language, "role_error"))
        return
    await session.commit()
    await message.answer(tr(language, "driver_switched_to_client"), reply_markup=main_menu(language))


@router.callback_query(F.data.startswith("role:"))
async def choose_role(callback: CallbackQuery, state: FSMContext, session):
    """Persist the selected role and start a one-time trial for a new driver."""
    state_data = await state.get_data()
    if (
        callback.message is None
        or callback.message.message_id != state_data.get("role_selection_message_id")
    ):
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                pass
        await callback.answer(tr("ru", "stale_keyboard"), show_alert=True)
        return
    user = await upsert_user(session, callback.from_user)
    language = user.language or "ru"
    role = callback.data.rsplit(":", 1)[1]
    if role not in {"client", "driver"}:
        await callback.answer(tr(language, "stale_keyboard"), show_alert=True)
        return
    await state.clear()
    if not await set_user_role(session, user.telegram_id, role):
        await callback.answer(tr(language, "role_error"), show_alert=True)
        return
    if role == "client":
        await callback.message.edit_text(tr(language, "welcome"), reply_markup=None)
        await callback.message.answer(tr(language, "client_menu_prompt"), reply_markup=main_menu(language))
        await callback.answer()
        return

    expires_at, trial_started = await start_trial_if_needed(session, user.telegram_id)
    shown_expiry = expires_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
    status_text = (
        tr(language, "driver_trial_started", expires_at=shown_expiry)
        if trial_started
        else (
            tr(language, "driver_subscription_active", expires_at=shown_expiry)
            if expires_at > datetime.now(UTC)
            else tr(language, "driver_subscription_expired")
        )
    )
    referral_bonus = await reward_driver_referral(session, user.telegram_id)
    await session.commit()
    if referral_bonus:
        inviter_id, inviter_expiry = referral_bonus
        inviter = await get_user(session, inviter_id)
        try:
            await callback.bot.send_message(
                inviter_id,
                tr(
                    (inviter.language if inviter else None),
                    "driver_referral_bonus",
                    expires_at=inviter_expiry.strftime("%d.%m.%Y %H:%M UTC"),
                ),
            )
        except TelegramAPIError:
            pass
    await callback.message.edit_text(status_text, reply_markup=None)
    await callback.message.answer(
        tr(language, "driver_welcome"),
        reply_markup=driver_menu(language, user.is_working),
    )
    available_routes = await popular_driver_routes(session, user.telegram_id)
    chosen_routes = {route.id for route in await get_driver_routes(session, user.telegram_id)}
    await callback.message.answer(
        tr(language, "driver_route_prompt"),
        reply_markup=driver_routes_keyboard(available_routes, chosen_routes, language),
    )
    await callback.answer()
