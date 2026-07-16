"""Тесты `services.notifier` — уведомление оператора через колбэк + маскирование ПДн."""

from __future__ import annotations

import asyncio

from services import notifier


def teardown_function() -> None:
    notifier.reset_operator_notifier()


def test_mask_pii_email() -> None:
    assert notifier.mask_pii("ivan@example.com") == "iv***@example.com"


def test_mask_pii_short() -> None:
    # Короткое значение без @ полностью скрывается.
    assert notifier.mask_pii("abcd") == "***"


def test_mask_pii_phone() -> None:
    masked = notifier.mask_pii("+79991234567")
    assert masked.startswith("+7") and masked.endswith("67") and "***" in masked


def test_notify_calls_registered_sender() -> None:
    received: list[str] = []

    async def sender(text: str) -> None:
        received.append(text)

    notifier.register_operator_notifier(sender)

    asyncio.run(
        notifier.notify_operator_brief_received(
            client_name="Иван", variant="individual", contact_value="ivan@example.com"
        )
    )
    assert len(received) == 1
    assert "Иван" in received[0]


def test_notify_no_sender_is_safe() -> None:
    # Колбэк не зарегистрирован — не должно падать.
    notifier.reset_operator_notifier()
    asyncio.run(notifier.notify_operator_brief_received(client_name="Иван", variant="community"))


def test_notify_swallows_sender_error() -> None:
    async def bad_sender(text: str) -> None:
        raise RuntimeError("telegram down")

    notifier.register_operator_notifier(bad_sender)
    # Ошибка отправки не должна пробрасываться (приём брифа не падает).
    asyncio.run(notifier.notify_operator_brief_received(client_name="Иван", variant="individual"))
