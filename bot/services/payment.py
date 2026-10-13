"""Merchant-independent payment request generation and signed webhook processing."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlencode
from uuid import uuid4

import qrcode
from sqlalchemy import select

from bot.config import settings
from bot.database.models import DriverSubscription, Transaction
from bot.services.subscription import extend_subscription, extend_vip_subscription


@dataclass(frozen=True)
class PaymentRequest:
    """Data required to display a QR payment request to a driver."""
    external_tx_id: str
    checkout_url: str
    amount: Decimal
    duration_days: int


@dataclass(frozen=True)
class PaymentResult:
    """Outcome returned to the webhook after an idempotent payment update."""
    driver_telegram_id: int
    status: str
    expires_at: datetime | None
    newly_applied: bool
    subscription_type: str = "regular"


async def create_payment_request(
    session, driver_telegram_id: int, duration_days: int, amount: Decimal | int,
    subscription_type: str = "regular",
) -> PaymentRequest:
    """Persist a pending transaction and create a placeholder provider checkout URL."""
    if duration_days not in {7, 30}:
        raise ValueError("Unsupported subscription duration")
    if subscription_type not in {"regular", "vip"}:
        raise ValueError("Unsupported subscription type")
    price = Decimal(str(amount)).quantize(Decimal("0.01"))
    if price <= 0:
        raise ValueError("Payment amount must be positive")
    transaction_id = uuid4().hex
    transaction = Transaction(
        driver_id=driver_telegram_id,
        external_tx_id=transaction_id,
        amount=price,
        duration_days=duration_days,
        subscription_type=subscription_type,
        status="pending",
    )
    session.add(transaction)
    await session.flush()
    query = urlencode({
        "external_tx_id": transaction_id,
        "custom_payload": str(driver_telegram_id),
        "amount": format(price, ".2f"),
        "currency": settings.payment_currency,
        "days": duration_days,
        "subscription_type": subscription_type,
    })
    separator = "&" if "?" in settings.payment_base_url else "?"
    return PaymentRequest(
        external_tx_id=transaction_id,
        checkout_url=f"{settings.payment_base_url}{separator}{query}",
        amount=price,
        duration_days=duration_days,
    )


def create_qr_png(checkout_url: str) -> bytes:
    """Encode a checkout URL as an in-memory PNG image for Telegram delivery."""
    image = qrcode.make(checkout_url)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def apply_payment_webhook(
    session,
    external_tx_id: str,
    driver_telegram_id: int,
    amount: Decimal,
    status: str,
    duration_days: int | None = None,
) -> PaymentResult:
    """Validate a provider event against its pending transaction and apply it once."""
    transaction = await session.scalar(
        select(Transaction)
        .where(Transaction.external_tx_id == external_tx_id)
        .with_for_update()
    )
    if transaction is None:
        raise ValueError("Unknown transaction")
    received_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if transaction.driver_id != driver_telegram_id or transaction.amount != received_amount:
        raise ValueError("Payment metadata does not match the pending transaction")
    if duration_days is not None and transaction.duration_days != duration_days:
        raise ValueError("Payment duration does not match the pending transaction")

    normalized_status = status.strip().upper()
    if normalized_status not in {"SUCCESS", "FAILED"}:
        raise ValueError("Unsupported payment status")
    if transaction.status == "success":
        expiry_column = (
            DriverSubscription.vip_expires_at
            if transaction.subscription_type == "vip"
            else DriverSubscription.expires_at
        )
        subscription = await session.scalar(
            select(expiry_column).where(DriverSubscription.driver_id == driver_telegram_id)
        )
        return PaymentResult(
            driver_telegram_id, "success", subscription, False, transaction.subscription_type
        )
    if transaction.status != "pending":
        raise ValueError("Transaction is no longer pending")
    if normalized_status == "FAILED":
        transaction.status = "failed"
        return PaymentResult(driver_telegram_id, "failed", None, False, transaction.subscription_type)

    extension = extend_vip_subscription if transaction.subscription_type == "vip" else extend_subscription
    expires_at = await extension(session, driver_telegram_id, transaction.duration_days)
    transaction.status = "success"
    return PaymentResult(driver_telegram_id, "success", expires_at, True, transaction.subscription_type)
