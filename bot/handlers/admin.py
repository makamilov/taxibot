import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.exc import IntegrityError
from bot.config import settings
from bot.database.requests import active_routes, admin_stats, create_route, deactivate_route, set_route_price, user_id_batches
from bot.keyboards.reply import main_menu

router = Router()
logger = logging.getLogger(__name__)

class BroadcastState(StatesGroup):
    """FSM state for receiving the administrator's broadcast text."""

    message = State()

def is_admin(message):
    """Return whether the sender's Telegram ID is configured as an administrator."""
    return message.chat.type == "private" and message.from_user.id in settings.admin_ids

@router.message(Command("admin"))
async def admin_panel(message: Message, session):
    """Show aggregate service statistics and the available admin commands."""
    if not is_admin(message):
        return await message.answer("Доступ запрещён.")
    users, active, completed = await admin_stats(session)
    await message.answer(f"Админ-панель\nПользователей: {users}\nАктивных заказов: {active}\nЗавершённых поездок: {completed}\n\nКоманды:\n/routes — направления и тарифы\n/setprice ID СУММА — изменить тариф\n/addrout ГОРОД | ГОРОД | СУММА — добавить направление\n/delroute ID — скрыть направление\n/broadcast — рассылка")

@router.message(Command("routes"))
async def routes_list(message: Message, session):
    """List currently active routes and their base prices for an administrator."""
    if not is_admin(message):
        return await message.answer("Доступ запрещён.")
    routes = await active_routes(session)
    await message.answer("Направления и базовые тарифы:\n" + "\n".join(f"{r.id}: {r.origin} — {r.destination}: {r.base_price}" for r in routes))

@router.message(Command("setprice"))
async def set_price(message: Message, session):
    """Validate and update a route price from the admin command arguments."""
    if not is_admin(message):
        return await message.answer("Доступ запрещён.")
    try:
        _, route_id, price = message.text.split(maxsplit=2)
        route_id, price = int(route_id), int(price)
        if price < 0:
            raise ValueError
    except (ValueError, TypeError):
        return await message.answer("Формат: /setprice ID СУММА")
    if not await set_route_price(session, route_id, price):
        return await message.answer("Направление не найдено. Проверьте /routes.")
    await message.answer("Базовый тариф обновлён.")

@router.message(Command("addrout"))
async def add_route(message: Message, session):
    """Create a new bounded route while safely handling duplicate route names."""
    if not is_admin(message):
        return await message.answer("Доступ запрещён.")
    try:
        parts = [part.strip() for part in message.text.partition(" ")[2].split("|")]
        if len(parts) != 3 or not parts[0] or not parts[1]:
            raise ValueError
        origin, destination, price = parts[0], parts[1], int(parts[2])
        if price < 0:
            raise ValueError
        async with session.begin_nested():
            await create_route(session, origin, destination, price)
    except IntegrityError:
        return await message.answer("Такое направление уже существует.")
    except ValueError:
        return await message.answer("Формат: /addrout ГОРОД ОТПРАВЛЕНИЯ | ГОРОД НАЗНАЧЕНИЯ | СУММА")
    await message.answer("Направление добавлено.")

@router.message(Command("delroute"))
async def remove_route(message: Message, session):
    """Deactivate a route without deleting orders that already refer to it."""
    if not is_admin(message):
        return await message.answer("Доступ запрещён.")
    try:
        route_id = int(message.text.split(maxsplit=1)[1])
    except (ValueError, IndexError):
        return await message.answer("Формат: /delroute ID")
    if not await deactivate_route(session, route_id):
        return await message.answer("Направление не найдено.")
    await message.answer("Направление скрыто от пассажиров. История заказов сохранена.")

@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    """Begin the protected admin broadcast flow."""
    if not is_admin(message):
        return await message.answer("Доступ запрещён.")
    await state.set_state(BroadcastState.message)
    await message.answer("Отправьте текст рассылки одним сообщением.")

@router.message(BroadcastState.message, F.text)
async def broadcast_send(message: Message, state: FSMContext, session):
    """Send a bounded text broadcast in small database batches with rate limiting."""
    if not is_admin(message):
        await state.clear()
        return
    if len(message.text) > 4096:
        await message.answer("Сообщение длиннее лимита Telegram (4096 символов). Отправьте более короткий текст.")
        return
    sent = failed = 0
    async for batch in user_id_batches(session):
        # End the read transaction before network I/O so a broadcast does not
        # hold a PostgreSQL connection while waiting on Telegram.
        await session.commit()
        for telegram_id in batch:
            try:
                await message.bot.send_message(telegram_id, message.text)
                sent += 1
            except TelegramAPIError as exc:
                logger.warning("Broadcast message delivery failed (%s)", type(exc).__name__)
                failed += 1
            await asyncio.sleep(0.05)
    await state.clear()
    await message.answer(f"Рассылка завершена. Доставлено: {sent}, ошибок: {failed}.", reply_markup=main_menu())
