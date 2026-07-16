"""UserbotClient.health: список сессий операторов и точечный health_for. Spec §6."""

import asyncio
from pathlib import Path

from tests._userbot_fakes import SENDER, FakeTelethon, make_client


def test_health_empty_when_no_sessions(tmp_path: Path) -> None:
    async def scenario() -> None:
        client, _ = make_client(tmp_path=str(tmp_path))
        assert await client.health() == {"sessions": []}

    asyncio.run(scenario())


def test_health_lists_authorized_session_with_phone(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(authorized=True, phone="+79990001122")
        client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
        result = await client.health()
        assert result == {
            "sessions": [{"sender_id": SENDER, "authorized": True, "phone": "+79990001122"}]
        }

    asyncio.run(scenario())


def test_health_two_senders_mixed_states(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake_a = FakeTelethon(authorized=True, phone="+79990001111")
        fake_b = FakeTelethon(authorized=False)
        client, _ = make_client(
            tmp_path=str(tmp_path),
            saved_for=(111, 222),
            fakes_by_sender={111: fake_a, 222: fake_b},
        )
        result = await client.health()
        assert result == {
            "sessions": [
                {"sender_id": 111, "authorized": True, "phone": "+79990001111"},
                {"sender_id": 222, "authorized": False},
            ]
        }

    asyncio.run(scenario())


def test_health_for_unknown_sender_unauthorized(tmp_path: Path) -> None:
    async def scenario() -> None:
        client, _ = make_client(tmp_path=str(tmp_path))
        assert await client.health_for(333) == {"sender_id": 333, "authorized": False}

    asyncio.run(scenario())
