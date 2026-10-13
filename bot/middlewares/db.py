import logging

from aiogram import BaseMiddleware
from bot.database.engine import SessionFactory

logger = logging.getLogger(__name__)

class DbSessionMiddleware(BaseMiddleware):
    """Attach an async SQLAlchemy session and transaction to every update."""

    async def __call__(self, handler, event, data):
        """Give each update its own transaction and log failures before rolling it back."""
        async with SessionFactory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                logger.exception("Update handler failed; database transaction rolled back")
                raise
