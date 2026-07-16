"""UserbotClient.send: успех, ошибки §9 и изоляция операторов (Telethon замокан)."""

import asyncio
from pathlib import Path

import pytest
from telethon import errors

from tests._userbot_fakes import SENDER, FakeTelethon, make_client


def test_send_ok_returns_none(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(authorized=True)
        client, fake = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
        error = await client.send(SENDER, "@ivanov", "привет, бриф тут")
        assert error is None
        assert fake.sent_messages == [("@ivanov", "привет, бриф тут")]

    asyncio.run(scenario())


def test_send_without_session_returns_sender_not_authorized(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Оператор ещё ни разу не проходил /link_userbot — файла сессии нет.
        client, _ = make_client(tmp_path=str(tmp_path))
        assert await client.send(SENDER, "@ivanov", "text") == "sender_not_authorized"

    asyncio.run(scenario())


def test_send_dead_session_returns_session_expired(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Сессия на диске есть, но клиент говорит is_user_authorized() == False.
        fake = FakeTelethon(authorized=False)
        client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
        assert await client.send(SENDER, "@ivanov", "text") == "session_expired"

    asyncio.run(scenario())


def test_send_uses_session_of_requested_sender_only(tmp_path: Path) -> None:
    """Авторизован только первый оператор — второй получает sender_not_authorized."""

    async def scenario() -> None:
        fake_a = FakeTelethon(authorized=True)
        client, _ = make_client(
            tmp_path=str(tmp_path),
            saved_for=(111,),
            fakes_by_sender={111: fake_a},
        )
        assert await client.send(111, "@ivanov", "text-a") is None
        assert fake_a.sent_messages == [("@ivanov", "text-a")]
        assert await client.send(222, "@petrov", "text-b") == "sender_not_authorized"

    asyncio.run(scenario())


def test_send_from_two_senders_uses_own_clients(tmp_path: Path) -> None:
    """Сообщения двух операторов уходят каждый через свой клиент."""

    async def scenario() -> None:
        fake_a = FakeTelethon(authorized=True)
        fake_b = FakeTelethon(authorized=True)
        client, _ = make_client(
            tmp_path=str(tmp_path),
            saved_for=(111, 222),
            fakes_by_sender={111: fake_a, 222: fake_b},
        )
        assert await client.send(111, "@ivanov", "from-a") is None
        assert await client.send(222, "@petrov", "from-b") is None
        assert fake_a.sent_messages == [("@ivanov", "from-a")]
        assert fake_b.sent_messages == [("@petrov", "from-b")]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (errors.UsernameNotOccupiedError(request=None), "username_not_occupied"),
        (errors.UsernameInvalidError(request=None), "username_invalid"),
        (errors.UserPrivacyRestrictedError(request=None), "privacy_restricted"),
        (errors.PeerFloodError(request=None), "peer_flood"),
        (errors.AuthKeyUnregisteredError(request=None), "session_expired"),
        (RuntimeError("boom"), "userbot_unreachable"),
    ],
)
def test_send_maps_errors(tmp_path: Path, exc: Exception, expected: str) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(authorized=True, send_error=exc)
        client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
        assert await client.send(SENDER, "@ivanov", "text") == expected

    asyncio.run(scenario())
