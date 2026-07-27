"""Посев кабинета из `.env` при старте ядра (spec 2026-07-27 §8.4).

Смысл переезда — без простоя: старт не должен падать, если VK недоступен или
ключ шифрования не задан, а уже добавленные кабинеты трогать нельзя.
"""

from __future__ import annotations

import asyncio

import core.app as core_app
import pytest
import services.ad_accounts as ad_accounts
from services.vk_identity import VkIdentity, VkUnreachableError


def test_seed_failure_does_not_break_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступная БД на старте — предупреждение в лог, а не падение контейнера."""

    def broken_sessionmaker() -> object:
        raise RuntimeError("database is not ready")

    monkeypatch.setattr(core_app, "get_sessionmaker", broken_sessionmaker)
    asyncio.run(core_app._seed_ad_account_from_env())


def test_seed_is_called_on_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Посев действительно вызывается в lifespan, а не остаётся мёртвым кодом."""
    called = {"n": 0}

    async def fake_seed() -> None:
        called["n"] += 1

    monkeypatch.setattr(core_app, "_seed_ad_account_from_env", fake_seed)
    monkeypatch.setattr(core_app, "register_telegram_notifier", lambda: None)

    async def run() -> None:
        async with core_app.lifespan(core_app.create_app()):
            pass

    asyncio.run(run())
    assert called["n"] == 1


def test_seed_swallows_vk_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK лежит — кабинет не заводится, но и исключение наружу не летит."""

    async def broken(token: str, **_: object) -> VkIdentity:
        raise VkUnreachableError("timeout")

    monkeypatch.setattr(ad_accounts, "fetch_identity", broken)

    def broken_sessionmaker() -> object:
        raise RuntimeError("no database in unit test")

    monkeypatch.setattr(core_app, "get_sessionmaker", broken_sessionmaker)
    asyncio.run(core_app._seed_ad_account_from_env())
