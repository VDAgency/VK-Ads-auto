"""Токен сообщества Senler в `bot/api_client` (respx-моки).

Дефект 2 (ревью 2026-08-24): при коде 500 `add_community_token` называл
причину ЛЮБОЙ внутренней ошибки «не задан ключ шифрования VK_ADS_SECRET_KEY»,
не заглядывая в тело ответа, — оператор шёл чинить не то. Приём тот же, что у
`add_ad_account` (`tests/test_bot_ad_accounts_handler.py` косвенно, здесь —
напрямую на уровне HTTP): узнанная причина — точный текст, неузнанная —
честное «внутренняя ошибка», без выдуманного диагноза.

Дефект 3: `delete_community_token` — новый клиентский вызов отвязки токена.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
import respx
from bot import api_client
from bot.api_client import CommunityTokenNotFound, CommunityTokenRejected, CoreUnavailable

_CORE = "http://api:8000"


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("bot.api_client.get_settings", lambda: SimpleNamespace(core_base_url=_CORE))


# --- add_community_token: дефект 2 ----------------------------------------------


def test_add_community_token_500_with_known_detail_names_the_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(500, json={"detail": "encryption_key_missing"})
            )
            with pytest.raises(CommunityTokenRejected) as exc_info:
                await api_client.add_community_token("token-value")
        assert "VK_ADS_SECRET_KEY" in str(exc_info.value)

    asyncio.run(scenario())


def test_add_community_token_500_with_unknown_detail_does_not_blame_the_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Главная репродукция дефекта 2: неопознанная причина 500 (внутренняя
    ошибка ядра, никак не связанная с ключом шифрования) раньше всё равно
    выдавалась как «не задан VK_ADS_SECRET_KEY» — ложный диагноз."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(500, json={"detail": "Internal Server Error"})
            )
            with pytest.raises(CommunityTokenRejected) as exc_info:
                await api_client.add_community_token("token-value")
        message = str(exc_info.value)
        assert "VK_ADS_SECRET_KEY" not in message
        assert "ключ шифрования" not in message

    asyncio.run(scenario())


def test_add_community_token_500_without_json_body_does_not_blame_the_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неожиданное исключение в ядре может вернуть 500 вообще без JSON-тела
    (стандартный ответ FastAPI на необработанный exception) — разбор тела не
    должен падать, а диагноз остаётся честным, не выдуманным."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(500, text="Internal Server Error")
            )
            with pytest.raises(CommunityTokenRejected) as exc_info:
                await api_client.add_community_token("token-value")
        assert "VK_ADS_SECRET_KEY" not in str(exc_info.value)

    asyncio.run(scenario())


def test_add_community_token_422_unreachable_detail_is_still_recognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Регресс: разбор 422 (`community_unreachable`) не задет правкой 500-ветки."""
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(422, json={"detail": "community_unreachable"})
            )
            with pytest.raises(CommunityTokenRejected) as exc_info:
                await api_client.add_community_token("token-value")
        assert "VK не подтвердил" in str(exc_info.value)

    asyncio.run(scenario())


def test_add_community_token_parses_success_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.post(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(
                    201,
                    json={
                        "community_id": "228817082",
                        "community_name": "DJ BEAUTY",
                        "connected": True,
                        "reason": "",
                    },
                )
            )
            result = await api_client.add_community_token("token-value")
        assert result.community_id == "228817082"
        assert result.connected is True

    asyncio.run(scenario())


# --- delete_community_token: дефект 3 --------------------------------------------


def test_delete_community_token_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.delete(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(204)
            )
            await api_client.delete_community_token("djbeauty")

    asyncio.run(scenario())


def test_delete_community_token_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.delete(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(404, json={"detail": "not_found"})
            )
            with pytest.raises(CommunityTokenNotFound):
                await api_client.delete_community_token("djbeauty")

    asyncio.run(scenario())


def test_delete_community_token_5xx_raises_core_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            router.delete(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(503)
            )
            with pytest.raises(CoreUnavailable):
                await api_client.delete_community_token("djbeauty")

    asyncio.run(scenario())


def test_delete_community_token_sends_the_reference_as_a_query_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)

    async def scenario() -> None:
        with respx.mock() as router:
            route = router.delete(f"{_CORE}/api/v1/senler/community-token").mock(
                return_value=httpx.Response(204)
            )
            await api_client.delete_community_token("djbeauty")
        assert route.calls.last.request.url.params["reference"] == "djbeauty"

    asyncio.run(scenario())
