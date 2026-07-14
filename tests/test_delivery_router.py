"""Тесты DeliveryRouter — детерминированный роутинг по типу контакта (§5 спеки)."""

import asyncio
from dataclasses import dataclass

from services.contact import Contact, ContactType
from services.delivery.base import DeliveryChannel, DeliveryResult
from services.delivery.router import DeliveryRouter


@dataclass
class _StubAdapter:
    """Минимальный адаптер, помечающий, какого он канала."""

    label: DeliveryChannel

    async def send(self, contact: Contact, invite_text: str) -> DeliveryResult:
        del contact, invite_text
        return DeliveryResult(ok=True, channel=self.label)


def _make_router() -> tuple[DeliveryRouter, _StubAdapter, _StubAdapter, _StubAdapter]:
    tg = _StubAdapter(DeliveryChannel.TELEGRAM)
    em = _StubAdapter(DeliveryChannel.EMAIL)
    mn = _StubAdapter(DeliveryChannel.MANUAL)
    return DeliveryRouter(telegram=tg, email=em, manual=mn), tg, em, mn


def test_telegram_contact_routes_to_telegram_adapter() -> None:
    router, tg, _, _ = _make_router()
    assert router.route(Contact(ContactType.TELEGRAM, "@ivanov")) is tg


def test_email_contact_routes_to_email_adapter() -> None:
    router, _, em, _ = _make_router()
    assert router.route(Contact(ContactType.EMAIL, "a@b.c")) is em


def test_phone_contact_routes_to_manual_adapter() -> None:
    router, _, _, mn = _make_router()
    assert router.route(Contact(ContactType.PHONE, "+79991234567")) is mn


def test_route_result_actually_sends_via_correct_adapter() -> None:
    """Интеграционная сборка: после `route()` вызов `send()` даёт канал того адаптера."""
    router, *_ = _make_router()

    async def scenario() -> tuple[DeliveryChannel, DeliveryChannel, DeliveryChannel]:
        tg_result = await router.route(Contact(ContactType.TELEGRAM, "@x")).send(
            Contact(ContactType.TELEGRAM, "@x"), "text"
        )
        em_result = await router.route(Contact(ContactType.EMAIL, "a@b.c")).send(
            Contact(ContactType.EMAIL, "a@b.c"), "text"
        )
        mn_result = await router.route(Contact(ContactType.PHONE, "+79991234567")).send(
            Contact(ContactType.PHONE, "+79991234567"), "text"
        )
        return tg_result.channel, em_result.channel, mn_result.channel

    tg_ch, em_ch, mn_ch = asyncio.run(scenario())
    assert tg_ch is DeliveryChannel.TELEGRAM
    assert em_ch is DeliveryChannel.EMAIL
    assert mn_ch is DeliveryChannel.MANUAL
