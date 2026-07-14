"""UserbotClient.health: authorized true/false (Telethon замокан). Spec §6."""

import asyncio
from pathlib import Path

from tests._userbot_fakes import FakeTelethon, make_client


def test_health_authorized_true_with_phone(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(authorized=True, phone="+79990001122")
        client, _ = make_client(fake, tmp_path=str(tmp_path), saved_session=True)
        result = await client.health()
        assert result["authorized"] is True
        assert result["phone"] == "+79990001122"

    asyncio.run(scenario())


def test_health_authorized_false_when_no_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        client, _ = make_client(tmp_path=str(tmp_path), saved_session=False)
        result = await client.health()
        assert result == {"authorized": False}

    asyncio.run(scenario())


def test_health_authorized_false_when_session_not_authorized(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(authorized=False)
        client, _ = make_client(fake, tmp_path=str(tmp_path), saved_session=True)
        result = await client.health()
        assert result == {"authorized": False}

    asyncio.run(scenario())
