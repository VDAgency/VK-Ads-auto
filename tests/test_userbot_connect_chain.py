"""Перебор точек подключения в `UserbotClient` — без единого сокета.

Сеть заходит в код через инъектируемую фабрику клиента, поэтому тест видит точный
порядок попыток и подсовывает «мёртвые» точки списком исключений.
"""

from __future__ import annotations

import asyncio

import pytest
from userbot.endpoints import Endpoint, EndpointResolver, parse_endpoints
from userbot.telethon_client import AuthError, UnreachableError

from tests._userbot_fakes import SENDER, FakeTelethon, make_client

_PROD_PINS = "2:149.154.167.51:5222,4:149.154.167.91:5222"


def _resolver(**kwargs: object) -> EndpointResolver:
    params: dict[str, object] = {"ports": (443, 5222), "auth_dc_order": (1, 4)}
    params.update(kwargs)
    return EndpointResolver(**params)  # type: ignore[arg-type]


def test_first_live_endpoint_wins(tmp_path: object) -> None:
    fake = FakeTelethon(authorized=True)
    record: list[Endpoint] = []
    client, _ = make_client(
        fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver(), record=record
    )
    assert asyncio.run(client.health_for(SENDER))["authorized"] is True
    assert len(record) == 1, "живая первая точка — перебирать дальше нечего"


def test_chain_moves_to_next_endpoint_after_failure(tmp_path: object) -> None:
    """Две точки мертвы, третья отвечает — подключение всё равно состоится."""
    fake = FakeTelethon(
        authorized=True,
        connect_errors=[ConnectionError("dead"), OSError("dead"), None],
    )
    record: list[Endpoint] = []
    client, _ = make_client(
        fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver(), record=record
    )
    assert asyncio.run(client.health_for(SENDER))["authorized"] is True
    assert len(record) == 3
    assert len({(e.ip, e.port) for e in record}) == 3, "каждая попытка — новая точка"


def test_all_endpoints_dead_reports_unreachable(tmp_path: object) -> None:
    """Недоступность сети — отдельная ошибка, а не «сессия мертва»."""
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead")] * 100)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    result = asyncio.run(client.health_for(SENDER))
    assert result["authorized"] is False
    assert result["error"] == "unreachable"


def test_send_reports_unreachable_not_expired(tmp_path: object) -> None:
    """Совет «перепривяжите юзербота» здесь был бы вредным: сессия может быть жива."""
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead")] * 100)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    error, _ = asyncio.run(client.send(SENDER, "@user", "текст"))
    assert error == "userbot_unreachable"


def test_failed_attempt_is_disconnected(tmp_path: object) -> None:
    """Клиент с упавшим connect() нельзя переиспользовать — его закрывают."""
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead"), None])
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,), resolver=_resolver())
    asyncio.run(client.health_for(SENDER))
    assert fake.connect_calls == 2


def test_auth_start_walks_configured_dc_order(tmp_path: object) -> None:
    record: list[Endpoint] = []
    client, _ = make_client(
        FakeTelethon(), tmp_path=str(tmp_path), resolver=_resolver(), record=record
    )
    asyncio.run(client.auth_start(SENDER, "+79990001122"))
    assert record[0].dc_id == 1, "новый логин начинаем с открытого дата-центра"


def test_auth_start_raises_unreachable_when_everything_is_dead(tmp_path: object) -> None:
    fake = FakeTelethon(connect_errors=[ConnectionError("dead")] * 100)
    client, _ = make_client(fake, tmp_path=str(tmp_path), resolver=_resolver())
    with pytest.raises(UnreachableError):
        asyncio.run(client.auth_start(SENDER, "+79990001122"))


def test_auth_start_does_not_leak_client_on_break(tmp_path: object) -> None:
    """Обрыв после установки соединения не должен оставлять висящий клиент."""

    class _BreakingFake(FakeTelethon):
        async def send_code_request(self, phone: str) -> object:
            raise ConnectionError("оборвалось на миграции")

    client, _ = make_client(_BreakingFake(), tmp_path=str(tmp_path), resolver=_resolver())
    with pytest.raises(UnreachableError):
        asyncio.run(client.auth_start(SENDER, "+79990001122"))
    # Клиент выброшен из pending — следующий шаг авторизации не подхватит мёртвый.
    with pytest.raises(AuthError) as exc_info:
        asyncio.run(client.auth_code(SENDER, "+79990001122", "12345", "hash"))
    assert exc_info.value.code == "no_pending_auth"


def test_existing_session_stays_within_its_datacenter(tmp_path: object) -> None:
    """Перебор для сохранённой сессии не выходит за её дата-центр."""
    resolver = EndpointResolver(pinned=parse_endpoints(_PROD_PINS))
    chain = resolver.candidates(4, session_endpoint=Endpoint(dc_id=4, ip="1.2.3.4", port=443))
    assert {endpoint.dc_id for endpoint in chain} == {4}
