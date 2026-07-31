"""Фоновая проверка сессий: кого проверяем, кого пропускаем, что при сбое."""

from __future__ import annotations

import asyncio
from pathlib import Path

from userbot.endpoints import EndpointResolver
from userbot.keepalive import keepalive_once
from userbot.state import SessionState

from tests._userbot_fakes import SENDER, FakeTelethon, make_client


def _resolver() -> EndpointResolver:
    return EndpointResolver(ports=(443,), auth_dc_order=(1,))


def test_first_run_checks_everyone(tmp_path: Path) -> None:
    client, _ = make_client(
        FakeTelethon(authorized=True),
        tmp_path=str(tmp_path),
        saved_for=(SENDER,),
        resolver=_resolver(),
    )
    assert asyncio.run(keepalive_once(client, clock=lambda: 0.0)) == [SENDER]
    assert client.health_for(SENDER)["state"] == "ready"


def test_terminal_sessions_are_skipped(tmp_path: Path) -> None:
    """Ретрай с отозванным ключом бесполезен, а Telegram реагирует на него хуже."""
    fake = FakeTelethon(authorized=True)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    client.states.mark_failed(SENDER, state=SessionState.EXPIRED, error="session_expired", now=0.0)
    assert asyncio.run(keepalive_once(client, clock=lambda: 10**6)) == []
    assert fake.connect_calls == 0


def test_backoff_delays_the_next_check(tmp_path: Path) -> None:
    """Мёртвый дата-центр не долбим каждые пять минут."""
    now = [0.0]
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead")] * 100)
    client, _ = make_client(
        fake,
        tmp_path=str(tmp_path),
        saved_for=(SENDER,),
        resolver=_resolver(),
        clock=lambda: now[0],
    )
    assert asyncio.run(keepalive_once(client, clock=lambda: now[0])) == [SENDER]
    assert client.health_for(SENDER)["state"] == "unreachable"

    # Сразу после неудачи проверять рано.
    now[0] = 60.0
    assert asyncio.run(keepalive_once(client, clock=lambda: now[0])) == []

    # После отката — снова можно.
    now[0] = 10**6
    assert asyncio.run(keepalive_once(client, clock=lambda: now[0])) == [SENDER]


def test_recovery_resets_backoff(tmp_path: Path) -> None:
    now = [0.0]
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead"), None])
    client, _ = make_client(
        fake,
        tmp_path=str(tmp_path),
        saved_for=(SENDER,),
        resolver=_resolver(),
        rounds=1,
        clock=lambda: now[0],
    )
    asyncio.run(keepalive_once(client, clock=lambda: now[0]))
    now[0] = 10**6
    asyncio.run(keepalive_once(client, clock=lambda: now[0]))
    assert client.health_for(SENDER)["state"] == "ready"

    # После успеха откат сброшен — проверяем без задержки.
    now[0] += 1
    assert asyncio.run(keepalive_once(client, clock=lambda: now[0])) == [SENDER]


def test_one_broken_session_does_not_stop_the_rest(tmp_path: Path) -> None:
    """Иначе один битый оператор заморозил бы состояние для всех остальных."""
    good = FakeTelethon(authorized=True)

    class _Exploding(FakeTelethon):
        async def connect(self) -> None:
            raise RuntimeError("что-то совсем неожиданное")

    client, _ = make_client(
        good,
        tmp_path=str(tmp_path),
        saved_for=(111, 222),
        resolver=_resolver(),
        fakes_by_sender={111: _Exploding(), 222: good},
    )
    checked = asyncio.run(keepalive_once(client, clock=lambda: 0.0))
    assert 222 in checked, "сбой одной сессии не должен мешать другой"


def test_absent_sessions_are_not_checked(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True)
    client, _ = make_client(fake, tmp_path=str(tmp_path), resolver=_resolver())
    assert asyncio.run(keepalive_once(client, clock=lambda: 0.0)) == []
    assert fake.connect_calls == 0
