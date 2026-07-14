"""UserbotClient.send: успех и все ошибки §9 (Telethon замокан). Spec §6, §9."""

import asyncio
from pathlib import Path

import pytest
from telethon import errors

from tests._userbot_fakes import FakeTelethon, make_client


def test_send_ok_returns_none(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(authorized=True)
        client, fake = make_client(fake, tmp_path=str(tmp_path), saved_session=True)
        error = await client.send("@ivanov", "привет, бриф тут")
        assert error is None
        assert fake.sent_messages == [("@ivanov", "привет, бриф тут")]

    asyncio.run(scenario())


def test_send_without_session_returns_session_expired(tmp_path: Path) -> None:
    async def scenario() -> None:
        # saved_session=False → store пуст → клиент не авторизован.
        client, _ = make_client(tmp_path=str(tmp_path), saved_session=False)
        assert await client.send("@ivanov", "text") == "session_expired"

    asyncio.run(scenario())


def test_send_unauthorized_session_returns_session_expired(tmp_path: Path) -> None:
    async def scenario() -> None:
        # Сессия на диске есть, но клиент говорит is_user_authorized() == False.
        fake = FakeTelethon(authorized=False)
        client, _ = make_client(fake, tmp_path=str(tmp_path), saved_session=True)
        assert await client.send("@ivanov", "text") == "session_expired"

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
        client, _ = make_client(fake, tmp_path=str(tmp_path), saved_session=True)
        assert await client.send("@ivanov", "text") == expected

    asyncio.run(scenario())
