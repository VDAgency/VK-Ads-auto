"""ManualDelivery — всегда ok=True, канал manual, fallback_text = исходный текст."""

import asyncio

from services.contact import Contact, ContactType
from services.delivery.base import DeliveryChannel
from services.delivery.manual import ManualDelivery


def test_manual_delivery_is_always_ok() -> None:
    async def scenario() -> None:
        result = await ManualDelivery().send(
            Contact(ContactType.PHONE, "+79991234567"), "приглашение"
        )
        assert result.ok is True
        assert result.channel is DeliveryChannel.MANUAL
        assert result.fallback_text == "приглашение"
        assert result.error is None

    asyncio.run(scenario())


def test_manual_delivery_passes_invite_text_verbatim() -> None:
    """Никаких «улучшений» текста — оператор получит ровно то, что дал сервис."""

    async def scenario() -> str | None:
        result = await ManualDelivery().send(
            Contact(ContactType.PHONE, "+79990000000"),
            "https://vk-ads-auto.ru/brief-individual.html?t=abc",
        )
        return result.fallback_text

    assert asyncio.run(scenario()) == "https://vk-ads-auto.ru/brief-individual.html?t=abc"
