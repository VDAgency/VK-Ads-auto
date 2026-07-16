"""Тесты `services.notifier_telegram` — HTTP-адаптер уведомлений оператору.

Проверяем: рассылку всем операторам, устойчивость к сбоям по одному получателю,
отсутствие токена бота в логах и регистрацию/no-op в зависимости от настроек.
HTTP мокается через respx.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
import respx
from config.settings import Settings
from pydantic import SecretStr
from services import notifier
from services.notifier_telegram import build_operator_sender, register_telegram_notifier

_TOKEN = "123:TEST"
_URL = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"


def teardown_function() -> None:
    notifier.reset_operator_notifier()


def test_sender_posts_to_all_operators() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            send = build_operator_sender(_TOKEN, [111, 222])
            await send("Клиент прислал бриф")
        assert route.call_count == 2
        bodies = [json.loads(call.request.content) for call in route.calls]
        assert bodies == [
            {"chat_id": 111, "text": "Клиент прислал бриф"},
            {"chat_id": 222, "text": "Клиент прислал бриф"},
        ]

    asyncio.run(scenario())


def test_one_failed_recipient_does_not_block_others() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(_URL).mock(
                side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"ok": True})]
            )
            send = build_operator_sender(_TOKEN, [111, 222])
            # Ошибка по первому получателю не пробрасывается — второй получает.
            await send("текст")
        assert route.call_count == 2

    asyncio.run(scenario())


def test_non_2xx_response_does_not_raise() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(_URL).mock(
                side_effect=[
                    httpx.Response(403, json={"ok": False, "description": "bot was blocked"}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            send = build_operator_sender(_TOKEN, [111, 222])
            await send("текст")
        assert route.call_count == 2

    asyncio.run(scenario())


def test_token_never_appears_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            # Текст исключения имитирует реальный: httpx кладёт URL с токеном.
            router.post(_URL).mock(
                side_effect=[
                    httpx.ConnectError(f"failed to connect to {_URL}"),
                    httpx.Response(500, text="oops"),
                ]
            )
            send = build_operator_sender(_TOKEN, [111, 222])
            with caplog.at_level(logging.WARNING):
                await send("текст")

    asyncio.run(scenario())
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2  # оба сбоя залогированы...
    assert _TOKEN not in caplog.text  # ...но токен в лог не попал


def test_register_is_noop_without_token() -> None:
    settings = Settings(
        _env_file=None,
        bot_token=SecretStr(""),
        operator_telegram_ids=frozenset({111}),
    )
    register_telegram_notifier(settings)

    async def scenario() -> None:
        with respx.mock(assert_all_called=False) as router:
            route = router.post(_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            await notifier.notify_operator_brief_received(client_name="Иван", variant="individual")
        assert route.call_count == 0  # колбэк не зарегистрирован — сети нет

    asyncio.run(scenario())


def test_register_and_notify_sends_http() -> None:
    settings = Settings(
        _env_file=None,
        bot_token=SecretStr(_TOKEN),
        operator_telegram_ids=frozenset({222, 111}),
    )
    register_telegram_notifier(settings)

    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
            await notifier.notify_operator_brief_received(
                client_name="Иван", variant="individual", contact_value="ivan@example.com"
            )
        assert route.call_count == 2
        chat_ids = [json.loads(call.request.content)["chat_id"] for call in route.calls]
        assert chat_ids == [111, 222]  # sorted() — детерминированный порядок

    asyncio.run(scenario())
