"""Тесты сшивки запуска РК (`services/launch_service`): заглушка/боевой выбор, персист."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from config.settings import Settings
from db.base import Base
from db.models import Account, Brief, Client
from db.repositories import get_creative_for_brief, get_latest_campaign_for_brief
from integrations.stub import StubAdapter
from integrations.vk_api import VkApiAdapter
from services.launch_service import _select_adapter, launch_from_creative
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_VALID = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "личная страница",
}


async def _with_db(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
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
        session.add(Client(id=1, account_id=1, full_name="Вячеслав", email="v@example.com"))
        session.add(
            Brief(id=1, account_id=1, client_id=1, variant="individual", payload=dict(_VALID))
        )
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_select_adapter_stub_without_token() -> None:
    adapter, status = _select_adapter(Settings(_env_file=None))
    assert isinstance(adapter, StubAdapter)
    assert status == "prepared"


def test_select_adapter_real_with_token_and_agency() -> None:
    settings = Settings(_env_file=None, vk_ads_access_token="tok", vk_agency_confirmed=True)
    adapter, status = _select_adapter(settings)
    assert isinstance(adapter, VkApiAdapter)
    assert status == "launched"


def test_select_adapter_stub_when_agency_not_confirmed() -> None:
    # Токен есть, но агентский статус не подтверждён → всё равно заглушка (CLAUDE.md §1.4).
    settings = Settings(_env_file=None, vk_ads_access_token="tok", vk_agency_confirmed=False)
    adapter, status = _select_adapter(settings)
    assert isinstance(adapter, StubAdapter)
    assert status == "prepared"


def test_launch_from_creative_prepares_campaign_and_persists() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, str, str]:
        outcome = await launch_from_creative(
            session,
            1,
            1,
            "photo",
            "/data/creatives/1/x.jpg",
            "Заголовок",
            "Текст",
            settings=Settings(_env_file=None),  # без токена → заглушка
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        creative = await get_creative_for_brief(session, 1, 1)
        assert campaign is not None and creative is not None
        return outcome.campaign_status, campaign.status, creative.file_path

    outcome_status, campaign_status, file_path = asyncio.run(_with_db(scenario))
    assert outcome_status == "prepared"
    assert campaign_status == "prepared"
    assert file_path == "/data/creatives/1/x.jpg"


def test_launch_persists_spec_and_stub_external_id() -> None:
    async def scenario(session: AsyncSession) -> tuple[str, dict[str, object]]:
        await launch_from_creative(
            session, 1, 1, "photo", "/x.jpg", None, None, settings=Settings(_env_file=None)
        )
        await session.commit()
        campaign = await get_latest_campaign_for_brief(session, 1, 1)
        assert campaign is not None
        return campaign.external_id or "", dict(campaign.spec_json)

    external_id, spec = asyncio.run(_with_db(scenario))
    assert external_id.startswith("stub-campaign")
    assert spec["objective"] == "socialengagement"
