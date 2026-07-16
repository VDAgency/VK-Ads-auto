"""TelegramUserbotDelivery — успех и все ошибки из §9 спеки (respx-моки httpx)."""

import asyncio
import json

import httpx
import respx
from services.contact import Contact, ContactType
from services.delivery.base import DeliveryChannel
from services.delivery.telegram import TelegramUserbotDelivery, human_message

_URL = "http://userbot:8001"
_SENDER = 111


def _contact() -> Contact:
    return Contact(ContactType.TELEGRAM, "@ivanov")


def _delivery() -> TelegramUserbotDelivery:
    return TelegramUserbotDelivery(_URL, sender_id=_SENDER)


def test_send_ok_returns_delivered() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            route = router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(200, json={"ok": True, "display_name": "Вячеслав"})
            )
            result = await _delivery().send(_contact(), "text")
        assert result.ok is True
        assert result.channel is DeliveryChannel.TELEGRAM
        assert result.fallback_text is None
        assert result.error is None
        # Имя получателя из Telegram прокидывается в результат доставки.
        assert result.recipient_name == "Вячеслав"
        # Отправитель уходит в userbot-сервис: сообщение шлётся от его сессии.
        body = json.loads(route.calls.last.request.content)
        assert body == {"sender_id": _SENDER, "username": "@ivanov", "text": "text"}

    asyncio.run(scenario())


def test_send_ok_without_display_name() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(return_value=httpx.Response(200, json={"ok": True}))
            result = await _delivery().send(_contact(), "text")
        assert result.ok is True
        assert result.recipient_name is None

    asyncio.run(scenario())


def test_send_username_not_occupied() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(
                    400, json={"ok": False, "error": "username_not_occupied"}
                )
            )
            result = await _delivery().send(_contact(), "text")
        assert result.ok is False
        assert result.error == "username_not_occupied"
        assert result.fallback_text == "text"

    asyncio.run(scenario())


def test_send_peer_flood_is_mapped() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(429, json={"ok": False, "error": "peer_flood"})
            )
            result = await _delivery().send(_contact(), "text")
        assert result.error == "peer_flood"

    asyncio.run(scenario())


def test_send_session_expired_is_mapped() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(401, json={"ok": False, "error": "session_expired"})
            )
            result = await _delivery().send(_contact(), "text")
        assert result.error == "session_expired"

    asyncio.run(scenario())


def test_send_sender_not_authorized_is_mapped() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(
                    401, json={"ok": False, "error": "sender_not_authorized"}
                )
            )
            result = await _delivery().send(_contact(), "text")
        assert result.error == "sender_not_authorized"
        assert result.fallback_text == "text"

    asyncio.run(scenario())


def test_send_privacy_restricted_is_mapped() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(403, json={"ok": False, "error": "privacy_restricted"})
            )
            result = await _delivery().send(_contact(), "text")
        assert result.error == "privacy_restricted"

    asyncio.run(scenario())


def test_send_timeout_becomes_userbot_unreachable() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(side_effect=httpx.ConnectTimeout("boom"))
            result = await _delivery().send(_contact(), "text")
        assert result.error == "userbot_unreachable"
        assert result.fallback_text == "text"

    asyncio.run(scenario())


def test_send_invalid_json_becomes_userbot_unreachable() -> None:
    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(500, text="<html>500</html>")
            )
            result = await _delivery().send(_contact(), "text")
        assert result.error == "userbot_unreachable"

    asyncio.run(scenario())


def test_send_unknown_error_code_falls_back_to_unreachable() -> None:
    """Незнакомый код от userbot не пробрасываем — сводим к предсказуемому."""

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_URL}/send").mock(
                return_value=httpx.Response(400, json={"ok": False, "error": "gibberish_xyz"})
            )
            result = await _delivery().send(_contact(), "text")
        assert result.error == "userbot_unreachable"

    asyncio.run(scenario())


def test_send_without_base_url_returns_unreachable_without_network() -> None:
    """Пустой USERBOT_BASE_URL — без попытки соединения."""

    async def scenario() -> None:
        result = await TelegramUserbotDelivery("", sender_id=_SENDER).send(_contact(), "text")
        assert result.error == "userbot_unreachable"

    asyncio.run(scenario())


def test_send_without_sender_fails_without_network() -> None:
    """Неизвестен отправитель (sender_id=None) — отказ сразу, без похода в сеть."""

    async def scenario() -> None:
        result = await TelegramUserbotDelivery(_URL).send(_contact(), "text")
        assert result.ok is False
        assert result.error == "sender_not_authorized"
        assert result.fallback_text == "text"

    asyncio.run(scenario())


def test_human_message_translates_known_codes() -> None:
    assert "не найден" in human_message("username_not_occupied")
    assert "разлогинен" in human_message("session_expired")
    assert "/link_userbot" in human_message("sender_not_authorized")
    # Незнакомые коды — общий текст, не крашится
    assert human_message("nothing_like_this") != ""
