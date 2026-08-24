"""Тесты эндпоинтов `/api/v1/cabinets` (PR-C + задача 2): list + detail + периоды + is_mock.

Задача 2: демо-гейт закрыт по умолчанию (дефект 2), в `StatsOut` есть `cpl` (дефект
3), эндпоинт `POST /{cabinet_id}/stats/sync` синкает метрики именно этого кабинета
перед показом (дефект 1) и никогда не роняет запрос 5xx-ом при сбое площадки.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import services.cabinet_stats as cabinet_stats_module
import services.stats_sync as stats_sync_module
from config.settings import Settings
from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Cabinet, Campaign, Client, Stat
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def _mock_settings(*, mock_stats_enabled: bool) -> Settings:
    return Settings(_env_file=None, mock_stats_enabled=mock_stats_enabled)


async def _call(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    method: str = "GET",
    with_stats: bool = False,
    with_stub_campaign: bool = False,
) -> tuple[int, dict[str, Any]]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(Account(id=1, name="default"))
        if with_stats:
            session.add(Stat(account_id=1, campaign_id="camp-1", shows=1000, clicks=50, spent=100))
        if with_stub_campaign:
            session.add(Client(id=1, account_id=1, full_name="Вячеслав"))
            session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
            session.add(
                Cabinet(
                    id=1,
                    account_id=1,
                    client_id=1,
                    channel="stub",
                    ad_object_url="https://vk.com/id1",
                )
            )
            session.add(
                Campaign(
                    id=1,
                    account_id=1,
                    brief_id=1,
                    cabinet_id=1,
                    status="launched",
                    objective="socialengagement",
                    external_id="stub-campaign-1",
                )
            )
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        if method == "POST":
            response = await client.post(path, params=params)
        else:
            response = await client.get(path, params=params)
    await engine.dispose()
    return response.status_code, response.json()


# --- дефект 2: демо-гейт закрыт по умолчанию ---------------------------------------


def test_list_returns_no_mock_cabinets_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cabinet_stats_module, "get_settings", lambda: _mock_settings(mock_stats_enabled=False)
    )
    code, data = asyncio.run(_call("/api/v1/cabinets"))
    assert code == 200
    assert data["items"] == []


def test_list_returns_mock_cabinets_when_flag_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cabinet_stats_module, "get_settings", lambda: _mock_settings(mock_stats_enabled=True)
    )
    code, data = asyncio.run(_call("/api/v1/cabinets"))
    assert code == 200
    assert data["items"]
    assert all(item["is_mock"] for item in data["items"])


def test_list_returns_real_when_campaign_exists() -> None:
    """Список кабинетов строится из кампаний, не из истории срезов `Stat` (A2)."""
    code, data = asyncio.run(_call("/api/v1/cabinets", with_stub_campaign=True))
    assert code == 200
    ids = [i["id"] for i in data["items"]]
    assert ids == ["stub-campaign-1"]
    assert data["items"][0]["is_mock"] is False
    assert data["items"][0]["status"] == "launched"


def test_stats_detail_real() -> None:
    code, data = asyncio.run(
        _call("/api/v1/cabinets/camp-1/stats", {"period": "all"}, with_stats=True)
    )
    assert code == 200
    assert data["shows"] == 1000
    assert data["ctr"] == 5.0
    assert data["cpc"] == 2.0
    assert data["is_mock"] is False


def test_stats_detail_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cabinet_stats_module, "get_settings", lambda: _mock_settings(mock_stats_enabled=True)
    )
    code, data = asyncio.run(_call("/api/v1/cabinets/demo-1/stats", {"period": "month"}))
    assert code == 200
    assert data["is_mock"] is True
    assert data["shows"] > 0


def test_stats_detail_honest_zero_when_mock_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без флага и без реальных данных — нули и `is_mock=False`, не выдуманные демо-цифры."""
    monkeypatch.setattr(
        cabinet_stats_module, "get_settings", lambda: _mock_settings(mock_stats_enabled=False)
    )
    code, data = asyncio.run(_call("/api/v1/cabinets/demo-1/stats", {"period": "all"}))
    assert code == 200
    assert data["is_mock"] is False
    assert data["shows"] == 0.0


def test_invalid_period_rejected() -> None:
    code, _ = asyncio.run(_call("/api/v1/cabinets/demo-1/stats", {"period": "year"}))
    assert code == 422


# --- дефект 3: CPL в ответе ---------------------------------------------------------


def test_stats_detail_includes_cpl() -> None:
    code, data = asyncio.run(
        _call("/api/v1/cabinets/camp-1/stats", {"period": "all"}, with_stats=True)
    )
    assert code == 200
    assert "cpl" in data
    assert data["cpl"] == 0.0  # results=0 в фикстуре with_stats — cpl честно нулевой


# --- дефект 1: явный синк по кабинету перед показом ---------------------------------
# A2/A3: три исхода наружу — `outcome` разводит «обновлено», «нечего обновлять»
# (кабинет есть, но кампания не активна, либо кабинета с таким id вовсе нет — оба
# случая дают пустую сводку и это НЕ сбой) и «не удалось» (настоящая ошибка
# площадки). `ok` остаётся для обратной совместимости, но теперь означает
# «не было настоящего сбоя» (`outcome != "failed"`) — это и есть решение дефекта:
# раньше `ok=False` на пустой сводке заставлял бота рисовать тревожную пометку под
# каждым неподнятым кабинетом.


def test_sync_cabinet_endpoint_persists_stat_for_matching_campaign() -> None:
    code, data = asyncio.run(
        _call(
            "/api/v1/cabinets/stub-campaign-1/stats/sync",
            method="POST",
            with_stub_campaign=True,
        )
    )
    assert code == 200
    assert data == {
        "ok": True,
        "outcome": "updated",
        "synced": 1,
        "failed": 0,
        "results": {"1": "ok"},
    }


def test_sync_cabinet_endpoint_does_not_touch_other_cabinets() -> None:
    """Синк по конкретному кабинету не должен цеплять кампании других кабинетов.

    Пустая сводка (A2) — честное «нечего обновлять» (`outcome="nothing_to_update"`),
    а не «обновлено» (раньше это ошибочно засчитывалось успехом) и не «не удалось»
    (это НЕ сбой площадки — синкать было нечего, о чём и говорит `ok=True`, A3).
    """

    code, data = asyncio.run(
        _call(
            "/api/v1/cabinets/some-other-cabinet/stats/sync",
            method="POST",
            with_stub_campaign=True,
        )
    )
    assert code == 200
    assert data == {
        "ok": True,
        "outcome": "nothing_to_update",
        "synced": 0,
        "failed": 0,
        "results": {},
    }


def test_sync_cabinet_endpoint_reports_failed_outcome_on_platform_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Настоящий сбой площадки — вот это `ok=False`/`outcome="failed"` (A3), не пустая
    сводка неактивной кампании.
    """

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("platform unreachable")

    monkeypatch.setattr(stats_sync_module, "fetch_campaign_stats", boom)
    code, data = asyncio.run(
        _call(
            "/api/v1/cabinets/stub-campaign-1/stats/sync",
            method="POST",
            with_stub_campaign=True,
        )
    )
    assert code == 200
    assert data == {
        "ok": False,
        "outcome": "failed",
        "synced": 0,
        "failed": 1,
        "results": {"1": "error"},
    }
