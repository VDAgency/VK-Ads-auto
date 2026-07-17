import asyncio

from config.settings import get_settings
from db.models import Discount, Referral
from db.repositories import create_client
from services.brief_parser import BriefVariant
from services.briefs import intake_brief
from services.referral import (
    generate_ref_code,
    referral_notification,
    register_referral,
    resolve_ref_code,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_brief_intake import VALID_INDIVIDUAL, _with_db

SECRET = "s"


def test_ref_code_roundtrip() -> None:
    assert resolve_ref_code(generate_ref_code(7, SECRET), SECRET) == 7


def test_ref_code_rejects_tampered_and_garbage() -> None:
    assert resolve_ref_code(generate_ref_code(7, SECRET) + "x", SECRET) is None
    assert resolve_ref_code("garbage!!!", SECRET) is None


def test_notification_text() -> None:
    assert "привёл" in referral_notification("Аня", "Петя", 10)


def test_register_referral_creates_discount() -> None:
    async def scenario(session: AsyncSession) -> Discount | None:
        referrer = await create_client(session, 1, "Реферер", "ref@e.com", None, None)
        referred = await create_client(session, 1, "Новый", "new@e.com", None, None)
        return await register_referral(session, 1, referrer.id, referred.id, month="2026-07")

    discount = asyncio.run(_with_db(scenario))
    assert discount is not None
    assert discount.percent == 10
    assert discount.month == "2026-07"


def test_self_referral_returns_none() -> None:
    async def scenario(session: AsyncSession) -> Discount | None:
        client = await create_client(session, 1, "Сам", "self@e.com", None, None)
        return await register_referral(session, 1, client.id, client.id, month="2026-07")

    assert asyncio.run(_with_db(scenario)) is None


def test_intake_with_ref_links_referral() -> None:
    async def scenario(session: AsyncSession) -> tuple[int, int]:
        referrer = await create_client(session, 1, "Реферер", "ref@e.com", None, None)
        await session.flush()
        code = generate_ref_code(referrer.id, get_settings().secret_key.get_secret_value())
        payload = {**VALID_INDIVIDUAL, "email": "brandnew@e.com"}
        await intake_brief(session, 1, BriefVariant.INDIVIDUAL, payload, ref_code=code)
        referrals = (await session.execute(select(Referral))).scalars().all()
        discounts = (await session.execute(select(Discount))).scalars().all()
        return len(referrals), len(discounts)

    referral_count, discount_count = asyncio.run(_with_db(scenario))
    assert referral_count == 1
    assert discount_count == 1


def _capture_operator_messages(scenario: object) -> list[str]:
    """Прогнать сценарий intake с перехватом уведомлений оператору."""
    from services.notifier import register_operator_notifier, reset_operator_notifier

    messages: list[str] = []

    async def capture(text: str) -> None:
        messages.append(text)

    register_operator_notifier(capture)
    try:
        asyncio.run(_with_db(scenario))  # type: ignore[arg-type]
    finally:
        reset_operator_notifier()
    return messages


def test_intake_with_ref_notifies_operator_about_referral() -> None:
    async def scenario(session: AsyncSession) -> None:
        referrer = await create_client(session, 1, "Реферер Рома", "ref@e.com", None, None)
        await session.flush()
        code = generate_ref_code(referrer.id, get_settings().secret_key.get_secret_value())
        payload = {**VALID_INDIVIDUAL, "email": "brandnew@e.com", "full_name": "Новичок Ник"}
        await intake_brief(session, 1, BriefVariant.INDIVIDUAL, payload, ref_code=code)

    messages = _capture_operator_messages(scenario)
    joined = " ".join(messages)
    assert "прислал бриф" in joined  # уведомление о самом брифе (Фаза 11)
    assert "привёл" in joined and "Реферер Рома" in joined  # уведомление о реферале (Фаза 10)


def test_self_serve_brief_notifies_operator_without_referral() -> None:
    async def scenario(session: AsyncSession) -> None:
        payload = {**VALID_INDIVIDUAL, "email": "solo@e.com", "full_name": "Соло Сэм"}
        # Ни инвайта, ни реф-кода — самостоятельный бриф.
        await intake_brief(session, 1, BriefVariant.INDIVIDUAL, payload)

    messages = _capture_operator_messages(scenario)
    assert any("прислал бриф" in m for m in messages)
    assert not any("привёл" in m for m in messages)  # реферала нет
