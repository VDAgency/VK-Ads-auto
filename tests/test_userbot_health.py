"""Чтение состояния сессий: `health`/`health_for` берут данные из памяти.

Проверка по сети теперь отдельная операция (`probe`), поэтому здесь её и проверяем
в паре: сначала проба наполняет состояние, потом чтение его отдаёт без сети.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from userbot.endpoints import EndpointResolver

from tests._userbot_fakes import SENDER, FakeTelethon, make_client


def _resolver() -> EndpointResolver:
    return EndpointResolver(ports=(443,), auth_dc_order=(1,))


def test_health_empty_when_no_sessions(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path=str(tmp_path), resolver=_resolver())
    assert client.health() == {"sessions": []}


def test_health_lists_session_after_probe(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, phone="+79990001122")
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    asyncio.run(client.probe(SENDER))

    sessions = client.health()["sessions"]
    assert isinstance(sessions, list)
    assert sessions[0]["sender_id"] == SENDER
    assert sessions[0]["authorized"] is True
    assert sessions[0]["state"] == "ready"
    assert sessions[0]["phone"] == "+79990001122"


def test_known_session_is_listed_before_any_probe(tmp_path: Path) -> None:
    """Сохранённая сессия должна быть видна сразу — со статусом «ещё не проверяли»."""
    client, _ = make_client(tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    sessions = client.health()["sessions"]
    assert isinstance(sessions, list)
    assert sessions[0]["state"] == "unknown"


def test_health_two_senders_mixed_states(tmp_path: Path) -> None:
    fake_a = FakeTelethon(authorized=True, phone="+79990001111")
    fake_b = FakeTelethon(authorized=False)
    client, _ = make_client(
        tmp_path=str(tmp_path),
        saved_for=(111, 222),
        fakes_by_sender={111: fake_a, 222: fake_b},
        resolver=_resolver(),
    )
    asyncio.run(client.probe(111))
    asyncio.run(client.probe(222))

    sessions = client.health()["sessions"]
    assert isinstance(sessions, list)
    assert {item["sender_id"]: item["state"] for item in sessions} == {
        111: "ready",
        222: "expired",
    }


def test_health_for_unknown_sender_is_absent(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path=str(tmp_path), resolver=_resolver())
    result = client.health_for(333)
    assert result["sender_id"] == 333
    assert result["state"] == "absent"
    assert result["authorized"] is False
