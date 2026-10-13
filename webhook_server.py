"""FastAPI endpoint for authenticated merchant payment notifications."""
import hashlib
import hmac
import json
import logging
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from bot.config import settings
from bot.database.engine import SessionFactory
from bot.database.models import User
from bot.i18n import tr
from bot.services.payment import apply_payment_webhook

logger = logging.getLogger(__name__)
MAX_WEBHOOK_BODY_BYTES = 16_384


def create_webhook_app(bot: Bot) -> FastAPI:
    """Create the payment webhook app bound to this running Telegram bot instance."""
    app = FastAPI(title="Taxi subscription payment webhook", docs_url=None, redoc_url=None)

    @app.post("/webhooks/payments", include_in_schema=False)
    async def receive_payment(request: Request):
        """Authenticate, validate, and idempotently apply one provider payment event."""
        secret = settings.payment_webhook_secret
        if not secret:
            raise HTTPException(status_code=503, detail="Payment webhook is not configured")
        body_chunks = []
        body_size = 0
        async for chunk in request.stream():
            body_size += len(chunk)
            if body_size > MAX_WEBHOOK_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Webhook body is too large")
            body_chunks.append(chunk)
        body = b"".join(body_chunks)
        received_signature = request.headers.get("X-Webhook-Signature", "").removeprefix("sha256=")
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(received_signature, expected_signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        try:
            payload = json.loads(body)
            external_tx_id = str(payload["external_tx_id"])
            driver_telegram_id = int(payload["custom_payload"])
            amount = Decimal(str(payload["amount"]))
            status = str(payload["status"])
            days_value = payload.get("days")
            duration_days = int(days_value) if days_value is not None else None
            if len(external_tx_id) > 128 or driver_telegram_id <= 0:
                raise ValueError("Invalid webhook identifiers")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise HTTPException(status_code=400, detail="Invalid payment event") from exc

        try:
            async with SessionFactory() as session:
                result = await apply_payment_webhook(
                    session,
                    external_tx_id,
                    driver_telegram_id,
                    amount,
                    status,
                    duration_days,
                )
                await session.commit()
                if result.newly_applied and result.expires_at is not None:
                    language = await session.scalar(
                        select(User.language).where(User.telegram_id == driver_telegram_id)
                    ) or "ru"
                    await session.commit()
                    local_expiry = result.expires_at.astimezone(
                        ZoneInfo(settings.timezone)
                    ).strftime("%d.%m.%Y %H:%M")
                    try:
                        await bot.send_message(
                            driver_telegram_id,
                            tr(
                                language,
                                "payment_success_vip" if result.subscription_type == "vip" else "payment_success",
                                expires_at=local_expiry,
                            ),
                        )
                    except TelegramAPIError:
                        logger.exception(
                            "Payment applied but Telegram notification failed for driver %s",
                            driver_telegram_id,
                        )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SQLAlchemyError as exc:
            logger.exception("Database error while processing payment webhook")
            raise HTTPException(status_code=500, detail="Payment processing failed") from exc
        return {"ok": True, "status": result.status}

    return app
