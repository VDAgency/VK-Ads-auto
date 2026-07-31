"""Жизненный цикл клиентов: кэш, локи, TTL незавершённой авторизации, закрытие.

Регресс на прод-инцидент: `/health` подключался к Telegram по каждой сессии, поэтому
один опрос занимал десятки секунд. Теперь чтение состояния сетью не пользуется вовсе,
и это здесь закреплено.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from telethon import errors
from userbot.endpoints import EndpointResolver
from userbot.state import SessionState
from userbot.telethon_client import AuthError

from tests._userbot_fakes import SENDER, FakeTelethon, make_client


def _resolver() -> EndpointResolver:
    return EndpointResolver(ports=(443,), auth_dc_order=(1,))


# --- чтение состояния не ходит в сеть -------------------------------------------


def test_health_never_touches_the_network(tmp_path: Path) -> None:
    """Прямой регресс: даже с мёртвым Telegram ответ должен быть мгновенным."""
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead")] * 100)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    payload = client.health()
    assert fake.connect_calls == 0, "чтение состояния обязано брать данные из памяти"
    assert payload["sessions"] == [
        {
            "sender_id": SENDER,
            "state": "unknown",
            "authorized": False,
            "phone": None,
            "endpoint": None,
            "error": None,
            "last_ok_at": None,
        }
    ]


def test_health_for_unknown_sender_is_absent(tmp_path: Path) -> None:
    client, _ = make_client(FakeTelethon(), tmp_path=str(tmp_path), resolver=_resolver())
    assert client.health_for(999)["state"] == "absent"


def test_probe_updates_state(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, phone="79990001122")
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    result = asyncio.run(client.probe(SENDER))
    assert result["state"] == "ready"
    assert result["phone"] == "79990001122"
    assert client.health_for(SENDER)["state"] == "ready", "состояние осело в памяти"


def test_probe_marks_unreachable_without_killing_session(tmp_path: Path) -> None:
    """Сеть недоступна — это не «разлогинен»: перепривязка не помогла бы."""
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead")] * 100)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    result = asyncio.run(client.probe(SENDER))
    assert result["state"] == "unreachable"


def test_probe_marks_expired_when_session_is_dead(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=False)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    assert asyncio.run(client.probe(SENDER))["state"] == "expired"


# --- кэш клиентов ---------------------------------------------------------------


def test_disconnected_client_is_rebuilt(tmp_path: Path) -> None:
    """Мёртвый клиент в кэше — источник бесконечных одинаковых ошибок."""
    fake = FakeTelethon(authorized=True)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    asyncio.run(client.probe(SENDER))
    first_calls = fake.connect_calls

    fake.connected = False  # соединение отвалилось между обращениями
    asyncio.run(client.probe(SENDER))
    assert fake.connect_calls > first_calls, "клиент должен быть пересобран"


def test_live_client_is_reused(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    asyncio.run(client.probe(SENDER))
    calls = fake.connect_calls
    asyncio.run(client.probe(SENDER))
    assert fake.connect_calls == calls, "живой клиент пересобирать незачем"


def test_concurrent_requests_build_one_client(tmp_path: Path) -> None:
    """Два клиента на одну сессию — прямой путь к отзыву ключа Telegram'ом."""
    fake = FakeTelethon(authorized=True)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())

    async def scenario() -> None:
        await asyncio.gather(*(client.probe(SENDER) for _ in range(5)))

    asyncio.run(scenario())
    assert fake.connect_calls == 1


def test_dead_session_error_drops_cached_client(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, send_error=errors.AuthKeyUnregisteredError(request=None))
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    error, _ = asyncio.run(client.send(SENDER, "@user", "текст"))
    assert error == "session_expired"
    assert client.health_for(SENDER)["state"] == "expired"


def test_recipient_error_keeps_session_intact(tmp_path: Path) -> None:
    """Неверный username ничего не говорит о состоянии сессии."""
    fake = FakeTelethon(authorized=True, send_error=errors.UsernameNotOccupiedError(request=None))
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    asyncio.run(client.probe(SENDER))
    error, _ = asyncio.run(client.send(SENDER, "@user", "текст"))
    assert error == "username_not_occupied"
    assert client.health_for(SENDER)["state"] == "ready"


# --- незавершённая авторизация ---------------------------------------------------


def test_pending_auth_expires(tmp_path: Path) -> None:
    """Оператор начал привязку и бросил — клиент не должен висеть до перезапуска."""
    now = [1000.0]
    client, _ = make_client(
        FakeTelethon(),
        tmp_path=str(tmp_path),
        resolver=_resolver(),
        pending_ttl=300.0,
        clock=lambda: now[0],
    )
    asyncio.run(client.auth_start(SENDER, "+79990001122"))

    now[0] += 301.0
    asyncio.run(client.auth_start(777, "+79990002233"))  # любой шаг чистит протухшие

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(client.auth_code(SENDER, "+79990001122", "12345", "hash"))
    assert exc_info.value.code == "no_pending_auth"


def test_wrong_code_drops_pending_client(tmp_path: Path) -> None:
    fake = FakeTelethon()
    client, _ = make_client(fake, tmp_path=str(tmp_path), resolver=_resolver())
    asyncio.run(client.auth_start(SENDER, "+79990001122"))

    fake.sign_in_error = errors.PhoneCodeInvalidError(request=None)
    with pytest.raises(AuthError):
        asyncio.run(client.auth_code(SENDER, "+79990001122", "00000", "hash"))

    with pytest.raises(AuthError) as exc_info:
        asyncio.run(client.auth_code(SENDER, "+79990001122", "11111", "hash"))
    assert exc_info.value.code == "no_pending_auth"


def test_two_factor_keeps_pending_client(tmp_path: Path) -> None:
    """При 2FA флоу продолжается — клиент нужен для шага с паролем."""
    client, _ = make_client(
        FakeTelethon(needs_2fa=True), tmp_path=str(tmp_path), resolver=_resolver()
    )
    asyncio.run(client.auth_start(SENDER, "+79990001122"))
    assert asyncio.run(client.auth_code(SENDER, "+79990001122", "12345", "hash")) is True
    asyncio.run(client.auth_password(SENDER, "пароль"))
    assert client.health_for(SENDER)["state"] in {"unknown", "ready"}


# --- закрытие --------------------------------------------------------------------


def test_close_disconnects_everything(tmp_path: Path) -> None:
    """Раньше ветки после yield в lifespan не было вовсе — сокеты утекали."""
    fake = FakeTelethon(authorized=True)
    pending = FakeTelethon()
    client, _ = make_client(
        fake,
        tmp_path=str(tmp_path),
        saved_for=(SENDER,),
        resolver=_resolver(),
        pending_queue=[pending],
    )
    asyncio.run(client.probe(SENDER))
    asyncio.run(client.auth_start(777, "+79990002233"))

    asyncio.run(client.close())

    assert fake.connected is False
    assert pending.connected is False


def test_absent_session_is_reported_not_probed(tmp_path: Path) -> None:
    fake = FakeTelethon()
    client, _ = make_client(fake, tmp_path=str(tmp_path), resolver=_resolver())
    assert asyncio.run(client.probe(999))["state"] == SessionState.ABSENT.value
    assert fake.connect_calls == 0
