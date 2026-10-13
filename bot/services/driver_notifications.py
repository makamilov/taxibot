"""Private Telegram fan-out for subscribed drivers and cancellation synchronization."""
import logging

from aiogram.exceptions import TelegramAPIError

from bot.database.requests import driver_order_notifications
from bot.i18n import tr
from bot.services.order_service import send_order_to_active_drivers

logger = logging.getLogger(__name__)


async def notify_active_drivers(bot, session, order_id: int, text: str, phone: str) -> int:
    """Compatibility wrapper delegating to the route-aware fan-out service."""
    return await send_order_to_active_drivers(bot, order_id)


async def update_driver_notification_cancelled(
    bot, chat_id: int, message_id: int, order_id: int
) -> None:
    """Edit a driver's order DM to show cancellation, falling back to a new notice."""
    text = tr("ru", "driver_order_cancelled", order_id=order_id)
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=None,
        )
    except TelegramAPIError:
        logger.exception("Could not edit driver DM for cancelled order %s", order_id)
        try:
            await bot.send_message(chat_id, text)
        except TelegramAPIError:
            logger.exception("Could not send driver cancellation notice for order %s", order_id)


async def synchronize_order_cancellation(bot, session, order_id: int) -> None:
    """Update every subscribed-driver DM linked to a cancelled order."""
    notifications = await driver_order_notifications(session, order_id)
    await session.commit()
    for notification in notifications:
        await update_driver_notification_cancelled(
            bot,
            notification.chat_id,
            notification.message_id,
            order_id,
        )
