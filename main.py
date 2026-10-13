import os

from dotenv import load_dotenv
import sentry_sdk

load_dotenv()
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    send_default_pii=True,
    traces_sample_rate=1.0,
)

import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from bot.config import settings
from bot.handlers import admin, client, common, driver, start
from bot.middlewares.db import DbSessionMiddleware
from bot.database.engine import engine
from bot.database.engine import SessionFactory
from bot.database.requests import ensure_default_routes
from webhook_server import create_webhook_app

async def run_bot_and_webhook(dp: Dispatcher, bot: Bot) -> None:
    """Run Telegram polling and the payment webhook concurrently with coordinated shutdown."""
    webhook = uvicorn.Server(uvicorn.Config(
        create_webhook_app(bot),
        host=settings.webhook_host,
        port=settings.webhook_port,
        log_level=settings.log_level.lower(),
    ))
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
        name="telegram-polling",
    )
    webhook_task = asyncio.create_task(webhook.serve(), name="payment-webhook")
    try:
        done, pending = await asyncio.wait(
            {polling_task, webhook_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            task.result()
    finally:
        webhook.should_exit = True
        if not webhook_task.done():
            await asyncio.wait({webhook_task}, timeout=5)
        for task in (polling_task, webhook_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(polling_task, webhook_task, return_exceptions=True)


async def main():
    """Initialize logging and route data, run polling, and close shared resources."""
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(DbSessionMiddleware())
    for router in (start.router, client.router, driver.router, admin.router, common.router):
        dp.include_router(router)
    try:
        async with SessionFactory() as session:
            await ensure_default_routes(session)
            await session.commit()
        await run_bot_and_webhook(dp, bot)
    except Exception:
        logging.getLogger(__name__).exception("Bot startup or polling failed")
        raise
    finally:
        await bot.session.close()
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
