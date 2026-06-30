"""Одноуровневая реферальная система (Блок 2).

Реф-код — подписанный (HMAC) бессрочный код клиента-реферера. По реф-ссылке
приходит новый клиент → фиксируем связь и начисляем рефереру скидку на 1 месяц
(размер согласуется с оператором; задел под автоматизацию платежей). Оператору —
уведомление «X привёл Y».
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from db.models import Discount
from db.repositories import create_discount, create_referral
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_DISCOUNT_PERCENT = 10


def generate_ref_code(client_id: int, secret: str) -> str:
    """Бессрочный реф-код клиента (подписан HMAC)."""
    signature = hmac.new(secret.encode(), str(client_id).encode(), hashlib.sha256).hexdigest()[:16]
    raw = f"{client_id}:{signature}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def resolve_ref_code(code: str, secret: str) -> int | None:
    """Достать client_id реферера из реф-кода или None (невалиден/подделан)."""
    try:
        padded = code + "=" * (-len(code) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        client_id_str, signature = raw.split(":")
    except (ValueError, UnicodeDecodeError):
        return None
    expected = hmac.new(secret.encode(), client_id_str.encode(), hashlib.sha256).hexdigest()[:16]
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        return int(client_id_str)
    except ValueError:
        return None


async def register_referral(
    session: AsyncSession,
    account_id: int,
    referrer_client_id: int,
    referred_client_id: int,
    month: str,
    percent: int = DEFAULT_DISCOUNT_PERCENT,
) -> Discount | None:
    """Зафиксировать реферал и начислить рефереру скидку. None при самореферале."""
    if referrer_client_id == referred_client_id:
        return None
    await create_referral(session, account_id, referrer_client_id, referred_client_id)
    return await create_discount(session, account_id, referrer_client_id, percent, month)


def referral_notification(referrer_name: str, referred_name: str, percent: int) -> str:
    """Текст уведомления оператору о новом реферале."""
    return f"{referrer_name} привёл(а) {referred_name}. Начислена скидка {percent}% на месяц."
